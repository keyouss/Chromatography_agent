import { afterEach, describe, expect, it, vi } from 'vitest'
import { getReplayDraft } from '@/shared/prediction/replay'

const profileSha = 'a'.repeat(64)

function draft(overrides: Record<string, unknown> = {}) {
  return {
    id: 'draft-1', project_id: 'project-1', source_record_id: 'record-1', source_prediction_id: 'prediction-1',
    chromatography_type: 'cc', tool: 'single', schema_version: '1.0',
    input: { column_spec_id: 'column', eluent_system_id: 'eluent', loading_solvent_id: 'solvent', sample_mass_mg: 100, loading_solvent_volume_ul: 300, flow_rate_ml_min: 10 },
    structure_refs: [{ type: 'structure', id: 'structure-1' }, { type: 'structure', id: 'structure-2' }],
    structure_snapshots: [
      { id: 'structure-1', project_id: 'project-1', canonical_smiles: 'C', stereochemistry_status: 'not_applicable' },
      { id: 'structure-2', project_id: 'project-1', canonical_smiles: 'CC', stereochemistry_status: 'not_applicable' },
    ],
    model_selection: { mode: 'exact', model_id: 'model-1', model_version: 'v1', preprocessing_profile_id: 'profile-1', preprocessing_profile_sha256: profileSha },
    source_model_snapshot: { id: 'model-1', version: 'v1', business_scope_id: 'cc', artifact_sha256: 'b'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0', preprocessing_profile_id: 'profile-1', preprocessing_profile_sha256: profileSha },
    revision: 1, source_record_revision: 1, etag: '"1"', created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z',
    ...overrides,
  }
}

function serve(value: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ data: value, request_id: 'replay-test', warnings: [] }), { status: 200, headers: { 'content-type': 'application/json' } })))
}

afterEach(() => vi.unstubAllGlobals())

describe('candidate profile replay identity', () => {
  it('accepts a profile-bound draft only when selection and source snapshot share the complete pair', async () => {
    serve(draft())
    await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).resolves.toMatchObject({
      model_selection: { preprocessing_profile_id: 'profile-1', preprocessing_profile_sha256: profileSha },
    })
  })

  it('rejects a source snapshot profile mismatch instead of dropping the identity fields', async () => {
    serve(draft({ source_model_snapshot: {
      id: 'model-1', version: 'v1', business_scope_id: 'cc', artifact_sha256: 'b'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0',
      preprocessing_profile_id: 'profile-2', preprocessing_profile_sha256: profileSha,
    } }))
    await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).rejects.toThrow('缺少必要字段')
  })

  it('keeps a legacy profileless CC draft readable for the page to present as read-only', async () => {
    const value = draft({
      model_selection: { mode: 'exact', model_id: 'model-1', model_version: 'v1' },
      source_model_snapshot: { id: 'model-1', version: 'v1', business_scope_id: 'cc', artifact_sha256: 'b'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0' },
    })
    serve(value)
    await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).resolves.toMatchObject({
      chromatography_type: 'cc', model_selection: { model_id: 'model-1', model_version: 'v1' },
    })
  })

  it('accepts the HPLC draft DTO empty modifier snapshot list when input has no modifiers', async () => {
    serve(draft({
      chromatography_type: 'hplc', schema_version: '1.1',
      input: { column_ids: ['ADH', 'IA'], flow_rate_ml_min: 1, isopropanol_fraction: 0.1, model_input_smiles: ['raw-one', 'raw-two'] },
      modifier_structure_snapshots: [],
    }))
    await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).resolves.toMatchObject({
      chromatography_type: 'hplc', modifier_structure_snapshots: [],
    })
  })

  it('rejects nullable and nonempty unbound modifier snapshots when the input has no modifiers', async () => {
    for (const snapshots of [null, [{ id: 'modifier-1', project_id: 'project-1', canonical_smiles: 'CO', stereochemistry_status: 'not_applicable' }]]) {
      serve(draft({ modifier_structure_snapshots: snapshots }))
      await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).rejects.toThrow('重放草稿')
    }
  })

  it('preserves SFC modifier reference and project binding checks', async () => {
    const snapshot = { id: 'modifier-1', project_id: 'project-1', canonical_smiles: 'CO', stereochemistry_status: 'not_applicable' }
    serve(draft({
      chromatography_type: 'sfc',
      input: { modifier_components: [{ structure_ref: 'modifier-1' }] },
      modifier_structure_snapshots: [snapshot],
    }))
    await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).resolves.toMatchObject({
      modifier_structure_snapshots: [snapshot],
    })

    serve(draft({
      chromatography_type: 'sfc',
      input: { modifier_components: [{ structure_ref: 'modifier-1' }] },
      modifier_structure_snapshots: [{ ...snapshot, id: 'other-modifier' }],
    }))
    await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).rejects.toThrow('Modifier 快照与结构引用或项目不匹配')
  })
})
