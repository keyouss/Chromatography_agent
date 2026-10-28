import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import PredictionView from '@/modules/prediction/PredictionView.vue'
import { usePredictionStore } from '@/shared/prediction/store'
import { saveReplayPointer } from '@/shared/prediction/replay'
import observedGcStartup from '../../testing/runs/2026-10-07-gc-readonly-preflight/acceptance-api-gc-models-startup.json'
import observedGcPrediction from '../../testing/runs/2026-10-07-gc-readonly-preflight/root-gc-browser-prediction-get.json'

vi.mock('@/app/generated/profile', () => ({
  activeProfile: {
    id: 'gc-v1', modules: ['prediction'], chromatographyTypes: ['gc'],
    predictionTools: { gc: ['single'] }, utilityFeatures: [], assistantModes: [],
  },
  profileIds: ['gc-v1'],
}))

const ready = observedGcStartup.type.data.health
const gcCapabilityData = observedGcStartup.type.data
const gcToolCapability = gcCapabilityData.tools[0]
const gcModel = gcCapabilityData.models[0]
const profileHash = gcModel.preprocessing_profile_sha256

function envelope(data: unknown, status = 200): Response {
  return new Response(JSON.stringify({ data, request_id: 'gc11-page-test', warnings: [] }), { status, headers: { 'content-type': 'application/json' } })
}

describe('GC 1.1 prediction page', () => {
  let requests: Array<{ url: string; method: string; body?: string; headers?: Record<string, string> }>
  let draft: unknown
  let deferCreate: boolean
  let pendingCreate: ((response: Response) => void) | null
  let predictionStatus: string
  let predictionModelOverride: Record<string, unknown> | null
  let predictionResult: unknown
  let predictionWarnings: Array<{ code: string; message: string }>
  let replayStorage: Map<string, string>

  beforeEach(() => {
    replayStorage = new Map()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => replayStorage.get(key) ?? null,
      setItem: (key: string, value: string) => replayStorage.set(key, String(value)),
      removeItem: (key: string) => replayStorage.delete(key),
      clear: () => replayStorage.clear(),
    })
    requests = []
    draft = null
    deferCreate = false
    pendingCreate = null
    predictionStatus = 'FAILED'
    predictionModelOverride = null
    predictionResult = undefined
    predictionWarnings = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      requests.push({ url, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? init.body : undefined, headers: init?.headers instanceof Headers ? Object.fromEntries(init.headers.entries()) : undefined })
      if (url.endsWith('/system/access-context')) return envelope({
        actor: { id: 'actor', auth_mode: 'deployment_default', policy_version: 'policy-1' }, actor_id: 'actor', auth_mode: 'deployment_default',
        allowed_actions: ['*'], permissions: ['*'], is_multi_user_authenticated: false, project_selection: 'resolved',
        default_project_id: 'priority-local', accessible_projects: [{ id: 'priority-local', name: 'Priority' }],
      })
      if (url.endsWith('/system/capabilities')) return envelope({ build_id: 'gc-v1', capabilities: [] })
      if (url.endsWith('/system/version')) return envelope({ application_version: '1.0', build_id: 'gc-v1', api_schema_version: '1.0', migration_version: '1', dependency_lock_hash: 'test' })
      if (url.endsWith('/prediction/types')) return envelope([{ id: 'gc', display_name: 'GC', tools: ['single'], health: ready }])
      if (url.endsWith('/prediction/types/gc')) return envelope(gcCapabilityData)
      if (url.endsWith('/prediction/types/cc')) return envelope({ id: 'cc', display_name: 'CC', tools: [], models: [], limits: { max_rows: 1, max_candidates: 1, max_evaluations: 1 }, health: { status: 'UNAVAILABLE', reason: 'test-disabled', checked_at: '' } })
      if (url.includes('/prediction/types/gc/tools/single/schema')) return envelope(gcToolCapability)
      if (url.includes('/prediction-drafts/')) return envelope(draft)
      if (url.endsWith('/structures/normalize')) {
        const body = JSON.parse(String(init?.body)) as { project_id: string; value: string }
        return envelope({ id: 'structure-gc', project_id: body.project_id, canonical_smiles: body.value, schema_version: '1.0', snapshot_hash: 'b'.repeat(64), created_at: '2026-10-07T00:00:00Z', is_valid: true, has_stereochemistry: false, stereochemistry_status: 'not_applicable', original_input: { input_type: 'smiles', value: body.value }, stereochemistry_warnings: [] })
      }
      if (url.endsWith('/predictions') && (init?.method ?? 'GET') === 'POST') {
        if (deferCreate) return new Promise<Response>((resolve) => { pendingCreate = resolve })
        return envelope({ id: 'accepted-gc', job_id: 'gc-job', prediction_record_id: 'gc-record', resource_ref: { type: 'prediction', id: 'accepted-gc' }, status: 'QUEUED' }, 202)
      }
      if (url.endsWith('/predictions/accepted-gc')) return envelope({
        id: 'accepted-gc', project_id: 'priority-local', prediction_record_id: 'gc-record', job_id: 'gc-job', chromatography_type: 'gc', tool: 'single', schema_version: '1.1', status: predictionStatus,
        model: predictionModelOverride ?? { id: gcModel.id, version: gcModel.version, business_scope_id: gcModel.scope, artifact_sha256: 'c'.repeat(64), input_schema_version: '1.1', output_schema_version: gcModel.output_schema_version, preprocessing_profile_id: gcModel.preprocessing_profile_id, preprocessing_profile_sha256: gcModel.preprocessing_profile_sha256 },
        result: predictionResult,
        input_snapshot: { input: { temperature_program: { mode: 'linear', initial_temperature_c: 50, final_temperature_c: 250, heating_rate_c_per_min: 10, initial_hold_min: 1 } }, normalized_structures: ['CC'], structure_refs: ['structure-gc'] },
        source: 'web', created_at: '2026-10-07T00:00:00Z', data_origin: 'production', revision: 1, etag: '"1"', updated_at: '2026-10-07T00:00:00Z', warnings: predictionWarnings,
      })
      throw new Error(`Unexpected request ${url}`)
    }))
  })

  async function openPage(path = '/prediction/gc') {
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/prediction/gc', component: PredictionView, meta: { chromatographicType: 'gc' } },
    ] })
    await router.push(path)
    await router.isReady()
    const pinia = createPinia()
    const wrapper = mount(PredictionView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    return { wrapper, pinia }
  }

  it('submits one GC 1.1 structure ref and the observed exact model/profile through the shared client', async () => {
    const { wrapper } = await openPage()
    expect(wrapper.find('.empty-state h2').exists()).toBe(false)
    expect(wrapper.get('.model-strip select').text()).toContain(gcModel.version)
    expect(requests.some((item) => item.url === '/api/v1/prediction/types/gc/tools/single/schema?schema_version=1.1')).toBe(true)
    const structure = wrapper.get('.structure-input')
    await structure.get('textarea').setValue('CC')
    await structure.get('.primary').trigger('click')
    await flushPromises()
    const submit = wrapper.get('[data-testid="gc-submit"]')
    expect((submit.element as HTMLButtonElement).disabled).toBe(false)
    await submit.trigger('click')
    await flushPromises()

    const create = requests.find((item) => item.url.endsWith('/predictions') && item.method === 'POST')
    expect(create?.headers?.['idempotency-key']).toBeTruthy()
    expect(JSON.parse(create!.body!)).toMatchObject({
      chromatography_type: 'gc', tool: 'single', schema_version: '1.1', structure_refs: ['structure-gc'],
      model_selection: { mode: 'exact', model_id: gcModel.id, model_version: gcModel.version, preprocessing_profile_id: gcModel.preprocessing_profile_id, preprocessing_profile_sha256: profileHash },
      input: { temperature_program: { mode: 'linear', initial_temperature_c: 50, final_temperature_c: 250, heating_rate_c_per_min: 10, initial_hold_min: 1 } },
    })
    wrapper.unmount()
  })

  it('keeps GC 1.0 replay input readable and readonly without backfilling a profile or posting', async () => {
    draft = {
      id: 'gc-draft', source_record_id: 'record-gc', source_prediction_id: 'prediction-gc', project_id: 'priority-local',
      chromatography_type: 'gc', tool: 'single', structure_refs: [{ type: 'structure', id: 'structure-gc' }],
      structure_snapshots: [{ id: 'structure-gc', project_id: 'priority-local', canonical_smiles: 'CC', stereochemistry_status: 'not_applicable' }],
      input: { temperature_program: { mode: 'linear', initial_temperature_c: 50, final_temperature_c: 200, heating_rate_c_per_min: 10, initial_hold_min: 1 } },
      schema_version: '1.0', model_selection: { mode: 'exact', model_id: gcModel.id, model_version: gcModel.version },
      source_model_snapshot: { id: gcModel.id, version: gcModel.version, business_scope_id: gcModel.scope, artifact_sha256: gcModel.artifact_sha256, input_schema_version: '1.0', output_schema_version: gcModel.output_schema_version },
      revision: 1, source_record_revision: 1, etag: '"1"', created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z',
    }
    const { wrapper } = await openPage('/prediction/gc?draft_id=gc-draft')
    expect(wrapper.get('[data-testid="replay-draft-unsupported"]').text()).toContain('GC 1.0 历史')
    expect(wrapper.text()).toContain('final_temperature_c')
    expect(wrapper.find('[data-testid="gc-submit"]').exists()).toBe(false)
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('does not rebind a GC 1.1 replay to the same model version with a different profile', async () => {
    const staleHash = 'b'.repeat(64)
    draft = {
      id: 'gc-draft-profile', source_record_id: 'record-gc', source_prediction_id: 'prediction-gc', project_id: 'priority-local',
      chromatography_type: 'gc', tool: 'single', structure_refs: [{ type: 'structure', id: 'structure-gc' }],
      structure_snapshots: [{ id: 'structure-gc', project_id: 'priority-local', canonical_smiles: 'CC', stereochemistry_status: 'not_applicable' }],
      input: { temperature_program: { mode: 'linear', initial_temperature_c: 50, final_temperature_c: 200, heating_rate_c_per_min: 10, initial_hold_min: 1 } },
      schema_version: '1.1', model_selection: { mode: 'exact', model_id: gcModel.id, model_version: gcModel.version, preprocessing_profile_id: 'old-profile', preprocessing_profile_sha256: staleHash },
      source_model_snapshot: { id: gcModel.id, version: gcModel.version, business_scope_id: gcModel.scope, artifact_sha256: gcModel.artifact_sha256, input_schema_version: '1.1', output_schema_version: gcModel.output_schema_version, preprocessing_profile_id: 'old-profile', preprocessing_profile_sha256: staleHash },
      revision: 1, source_record_revision: 1, etag: '"1"', created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z',
    }
    const { wrapper } = await openPage('/prediction/gc?draft_id=gc-draft-profile')
    expect(wrapper.get('[data-testid="replay-draft-status"]').text()).toContain('gc-draft-profile')
    expect(wrapper.get('[role="alert"]').text()).toContain('预处理版本与当前能力不一致')
    expect(wrapper.find('[data-testid="gc-submit"]').exists()).toBe(false)
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  function mismatchedTypeDraft() {
    return {
      id: 'test-only-tlc-draft', source_record_id: 'test-only-record', source_prediction_id: 'test-only-prediction', project_id: 'priority-local',
      chromatography_type: 'tlc', tool: 'single', structure_refs: [{ type: 'structure', id: 'test-only-structure' }],
      structure_snapshots: [{ id: 'test-only-structure', project_id: 'priority-local', canonical_smiles: 'CC', stereochemistry_status: 'not_applicable' }],
      input: { solvent_components: [] }, schema_version: '1.0', model_selection: { mode: 'exact', model_id: gcModel.id, model_version: gcModel.version },
      source_model_snapshot: { id: gcModel.id, version: gcModel.version, business_scope_id: gcModel.scope, artifact_sha256: gcModel.artifact_sha256, input_schema_version: '1.0', output_schema_version: gcModel.output_schema_version },
      revision: 1, source_record_revision: 1, etag: '"test-only"', created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z',
    }
  }

  it('ignores a valid other-type project pointer while keeping GC capability ready and preserving the pointer', async () => {
    draft = mismatchedTypeDraft()
    saveReplayPointer({ project_id: 'priority-local', draft_id: 'test-only-tlc-draft' })

    const { wrapper } = await openPage()

    expect(wrapper.get('.model-strip select').text()).toContain(gcModel.version)
    expect(wrapper.find('[data-testid="replay-draft-blocked"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="gc-capability-missing"]').exists()).toBe(false)
    expect(localStorage.getItem('chrom-platform.replay-draft')).toBe(JSON.stringify({ 'priority-local': 'test-only-tlc-draft' }))
    expect(requests.some((request) => request.url.endsWith('/prediction-drafts/test-only-tlc-draft?project_id=priority-local'))).toBe(true)
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('keeps an explicit other-type draft route blocked and does not clear its pointer', async () => {
    draft = mismatchedTypeDraft()
    saveReplayPointer({ project_id: 'priority-local', draft_id: 'test-only-tlc-draft' })

    const { wrapper } = await openPage('/prediction/gc?draft_id=test-only-tlc-draft')

    expect(wrapper.get('[role="alert"]').text()).toContain('草稿与当前项目或色谱类型不匹配')
    expect(wrapper.find('[data-testid="replay-draft-blocked"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="gc-submit"]').exists()).toBe(false)
    expect(localStorage.getItem('chrom-platform.replay-draft')).toBe(JSON.stringify({ 'priority-local': 'test-only-tlc-draft' }))
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('hides a GC 1.1 result when the returned model snapshot omits its profile pair', async () => {
    predictionStatus = 'SUCCEEDED'
    predictionModelOverride = { id: gcModel.id, version: gcModel.version, business_scope_id: gcModel.scope, artifact_sha256: 'c'.repeat(64), input_schema_version: '1.1', output_schema_version: gcModel.output_schema_version }
    predictionResult = { task_type: 'gc', schema_version: gcModel.output_schema_version, applicability: { supported_scope: 'gc-retention' }, rows: [{ retention_time_min: 123.4, quantiles: { q50: 123.4 } }] }
    const { wrapper } = await openPage()
    const structure = wrapper.get('.structure-input')
    await structure.get('textarea').setValue('CC')
    await structure.get('.primary').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="gc-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('GC 1.1 返回模型或预处理身份与本次精确选择不匹配')
    expect(wrapper.get('.result-panel').text()).not.toContain('123.4')
    wrapper.unmount()
  })

  it('renders observed GC applicability warnings and deduplicates a top-level copy', async () => {
    predictionStatus = 'SUCCEEDED'
    const warnings = observedGcPrediction.result.applicability.warnings
    predictionResult = observedGcPrediction.result
    predictionWarnings = [{ code: warnings[0].code, message: warnings[0].message }]
    const { wrapper } = await openPage()
    const structure = wrapper.get('.structure-input')
    await structure.get('textarea').setValue('CC')
    await structure.get('.primary').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="gc-submit"]').trigger('click')
    await flushPromises()

    const applicabilityRows = wrapper.findAll('[data-testid="gc-applicability-warnings"] p')
    expect(applicabilityRows).toHaveLength(warnings.length - 1)
    const resultText = wrapper.get('.result-panel').text()
    for (const warning of warnings) {
      expect(resultText.split(warning.message).length - 1).toBe(1)
    }
    for (const warning of warnings.slice(1)) {
      expect(resultText).toContain(warning.code)
    }
    wrapper.unmount()
  })

  it('hides a GC 1.1 result bound to a different profile despite matching model id and version', async () => {
    predictionStatus = 'SUCCEEDED'
    predictionModelOverride = { id: gcModel.id, version: gcModel.version, business_scope_id: gcModel.scope, artifact_sha256: 'c'.repeat(64), input_schema_version: '1.1', output_schema_version: gcModel.output_schema_version, preprocessing_profile_id: 'other-profile', preprocessing_profile_sha256: 'b'.repeat(64) }
    predictionResult = { task_type: 'gc', schema_version: gcModel.output_schema_version, applicability: { supported_scope: 'gc-retention' }, rows: [{ retention_time_min: 123.4, quantiles: { q50: 123.4 } }] }
    const { wrapper } = await openPage()
    const structure = wrapper.get('.structure-input')
    await structure.get('textarea').setValue('CC')
    await structure.get('.primary').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="gc-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('GC 1.1 返回模型或预处理身份与本次精确选择不匹配')
    expect(wrapper.get('.result-panel').text()).not.toContain('123.4')
    wrapper.unmount()
  })

  it('ignores a late GC create response after same-project type loading advances', async () => {
    const { wrapper, pinia } = await openPage()
    const structure = wrapper.get('.structure-input')
    await structure.get('textarea').setValue('CC')
    await structure.get('.primary').trigger('click')
    await flushPromises()
    deferCreate = true
    await wrapper.get('[data-testid="gc-submit"]').trigger('click')
    await flushPromises()
    expect(pendingCreate).toBeTypeOf('function')

    const store = usePredictionStore(pinia)
    store.selectedType = 'cc'
    await flushPromises()
    pendingCreate!(envelope({ id: 'accepted-gc', job_id: 'gc-job', prediction_record_id: 'gc-record', resource_ref: { type: 'prediction', id: 'accepted-gc' }, status: 'QUEUED' }, 202))
    await flushPromises()
    expect(store.accepted).toBeNull()
    expect(requests.some((request) => request.url.endsWith('/predictions/accepted-gc'))).toBe(false)
    wrapper.unmount()
  })
})
