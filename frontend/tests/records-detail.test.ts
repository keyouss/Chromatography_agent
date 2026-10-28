import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { ApiClientError } from '@/core/api/client'
import { clearReplayPointer, createReplayDraft, getReplayDraft, readReplayPointer } from '@/shared/prediction/replay'
import { usePredictionStore } from '@/shared/prediction/store'
import type { ContractPrediction, ContractPredictionRecord } from '@/core/contract-types'
import PredictionRecordsView from '@/modules/records/PredictionRecordsView.vue'
import { useRecordsStore } from '@/modules/records/store'
import { useAppStore } from '@/stores/app'
import { activeProfile } from '@/app/generated/profile'

describe('record replay pointer and API', () => {
  const draft = {
    id: 'draft-1', source_record_id: 'record-1', source_prediction_id: 'prediction-1', project_id: 'project-1', chromatography_type: 'gc', tool: 'single',
    structure_refs: [{ type: 'structure' as const, id: 'structure-1' }], structure_snapshots: [{ id: 'structure-1', project_id: 'project-1', canonical_smiles: 'CC', stereochemistry_status: 'not_applicable' }],
    input: { temperature_program: { mode: 'linear', initial_temperature_c: 50, final_temperature_c: 200, heating_rate_c_per_min: 10, initial_hold_min: 1 } },
    schema_version: 'gc-v1', model_selection: { mode: 'exact' as const, model_id: 'model-1', model_version: '1.0' },
    source_model_snapshot: { id: 'model-1', version: '1.0', business_scope_id: 'gc-default', artifact_sha256: 'a'.repeat(64), input_schema_version: 'gc-v1', output_schema_version: 'gc-result-v1' },
    source_record_revision: 1, revision: 1, etag: 'draft-etag-1', created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z',
  }
  let stored = new Map<string, string>()
  beforeEach(() => {
    stored = new Map()
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => stored.set(key, String(value)),
      removeItem: (key: string) => stored.delete(key),
      clear: () => stored.clear(),
    })
  })
  afterEach(() => { vi.unstubAllGlobals() })

  it('retries a failed POST with the same key and persists only the project/draft pointer', async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('network unavailable')).mockResolvedValueOnce(new Response(JSON.stringify({ data: draft }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(createReplayDraft('record-1', 'project-1')).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' })
    await expect(createReplayDraft('record-1', 'project-1')).resolves.toMatchObject({ id: 'draft-1' })
    const firstHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
    const secondHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers)
    expect(firstHeaders.get('Idempotency-Key')).toBe(secondHeaders.get('Idempotency-Key'))
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({})
    expect(JSON.parse(stored.get('chrom-platform.replay-draft') ?? '{}')).toEqual({ 'project-1': 'draft-1' })
  })

  it('restores from GET and clears only the matching project pointer', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: draft }), { headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).resolves.toMatchObject({ id: 'draft-1' })
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/prediction-drafts/draft-1?project_id=project-1')
    stored.set('chrom-platform.replay-draft', JSON.stringify({ 'project-1': 'draft-1', 'project-2': 'draft-2' }))
    clearReplayPointer({ project_id: 'project-1', draft_id: 'draft-1' })
    expect(readReplayPointer('project-1')).toBeNull()
    expect(readReplayPointer('project-2')).toEqual({ project_id: 'project-2', draft_id: 'draft-2' })
  })

  it('sends archive using only the frozen confirmation body and required idempotency header', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: record, request_id: 'archive' }), { headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    await useRecordsStore().archive('record-1', 'confirmation-1')
    const request = fetchMock.mock.calls[0]!
    const headers = new Headers(request[1]?.headers)
    expect(JSON.parse(String(request[1]?.body))).toEqual({ confirmation_id: 'confirmation-1' })
    expect(headers.get('Idempotency-Key')).toBeTruthy()
    expect(headers.get('If-Match')).toBeNull()
  })

  it('sends shared archive and replay with only their declared headers and bodies', async () => {
    setActivePinia(createPinia())
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: {}, request_id: 'archive-shared' }), { headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: {}, request_id: 'replay-shared' }), { headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetchMock)
    const store = usePredictionStore()
    await store.archiveRecord('record-shared', 'confirmation-shared')
    await store.replayRecord('record-shared')
    const archive = fetchMock.mock.calls[0]!
    const replay = fetchMock.mock.calls[1]!
    const archiveHeaders = new Headers(archive[1]?.headers)
    const replayHeaders = new Headers(replay[1]?.headers)
    expect(JSON.parse(String(archive[1]?.body))).toEqual({ confirmation_id: 'confirmation-shared' })
    expect(JSON.parse(String(replay[1]?.body))).toEqual({})
    expect(archiveHeaders.get('Idempotency-Key')).toBeTruthy()
    expect(replayHeaders.get('Idempotency-Key')).toBeTruthy()
    expect(archiveHeaders.get('If-Match')).toBeNull()
    expect(replayHeaders.get('If-Match')).toBeNull()
  })

  it('surfaces forbidden/missing GET responses as API errors', async () => {
    for (const status of [403, 404]) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status })))
      await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).rejects.toBeInstanceOf(ApiClientError)
    }
  })

  it('rejects incomplete or internally mismatched frozen draft DTOs', async () => {
    const invalidDrafts = [
      { ...draft, source_prediction_id: undefined },
      { ...draft, source_record_revision: undefined },
      { ...draft, revision: undefined },
      { ...draft, etag: undefined },
      { ...draft, created_at: undefined },
      { ...draft, updated_at: undefined },
      { ...draft, source_model_snapshot: undefined },
      { ...draft, model_selection: { mode: 'champion', model_id: 'model-1', model_version: '1.0' } },
      { ...draft, model_selection: { ...draft.model_selection, model_id: 'other-model' } },
      { ...draft, structure_refs: undefined },
    ]
    for (const invalid of invalidDrafts) {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: invalid }), { headers: { 'Content-Type': 'application/json' } })))
      await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).rejects.toThrow('重放草稿')
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ data: { ...draft, project_id: 'other-project' } }), { headers: { 'Content-Type': 'application/json' } })))
    await expect(getReplayDraft({ project_id: 'project-1', draft_id: 'draft-1' })).rejects.toBeInstanceOf(Error)
  })
})

const record = {
  id: 'record_readonly_1', project_id: 'priority-local', prediction_id: 'pred_readonly_1', batch_id: null,
  source: 'web', actor_id: 'private-actor', chromatography_type: 'tlc',
  model_snapshot: { id: 'tlc-model', version: 'v1.1', artifact_sha256: 'a'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0', private_path: 'C:\\private\\weights.bin' },
  input_snapshot: { structure_refs: ['str_readonly_1'], normalized_structures: ['CCO'], input: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.5 }, { solvent_id: 'ethyl_acetate', fraction: 0.5 }] } },
  result_ref: { type: 'prediction', id: 'pred_readonly_1' }, warnings: [], status: 'SUCCEEDED', archived_at: null,
  created_at: '2026-10-03T10:00:00Z', artifact_refs: [], revision: 2, etag: 'etag-readonly-1',
} as unknown as ContractPredictionRecord

const prediction = {
  id: 'pred_readonly_1', project_id: 'priority-local', prediction_record_id: record.id, job_id: 'job_readonly_1',
  chromatography_type: 'tlc', tool: 'single', schema_version: '1.0', status: 'SUCCEEDED',
  model: { id: 'tlc-model', version: 'v1.1', business_scope_id: 'tlc-default', artifact_sha256: 'a'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0', score_semantics: null, threshold: null },
  input_snapshot: { structure_refs: ['str_readonly_1'], normalized_structures: ['CCO'], input: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.5 }, { solvent_id: 'ethyl_acetate', fraction: 0.5 }] } },
  result: {
    task_type: 'tlc', schema_version: '1.0', model_id: 'tlc-model', model_version: 'v1.1', artifact_sha256: 'a'.repeat(64),
    ensemble_weights: { ann: 0.4 }, mobile_phase_original: { hexane: 0.5, ethyl_acetate: 0.5 }, mobile_phase_normalized: { hexane: 0.5, ethyl_acetate: 0.5 },
    rows: [{ rf: 0.222364 }], applicability: { supported_scope: 'TLC 二元展开剂', warnings: [
      { code: 'RATIO_NORMALIZED', message: '比例已归一化', field: 'input.solvent_components' },
      { code: 'EXPERIMENTAL_CONFIRMATION_REQUIRED', message: '需要实验确认', field: null },
      { code: 'NO_INDEPENDENT_GENERALIZATION_EVIDENCE', message: '暂无独立泛化证据', field: null },
    ] }, metrics: [], visualizations: [],
  },
  warnings: [], error: null, source: 'web', conversation_id: null, agent_run_id: null,
  created_at: '2026-10-03T10:00:00Z', completed_at: '2026-10-03T10:00:01Z', data_origin: 'production', revision: 2, etag: 'prediction-etag', updated_at: '2026-10-03T10:00:01Z',
} as unknown as ContractPrediction

const hplcRecord = {
  ...record,
  id: 'record_hplc_metrics_1', prediction_id: 'pred_hplc_metrics_1', chromatography_type: 'hplc',
  input_snapshot: { structure_refs: ['str_compound_1', 'str_compound_2'], normalized_structures: ['C[C@H](N)C(=O)O', 'C[C@@H](N)C(=O)O'], input: { column_ids: ['ADH', 'IA'], flow_rate_ml_min: 1, isopropanol_fraction: 0.1 } },
} as unknown as ContractPredictionRecord

const hplcPrediction = {
  ...prediction,
  id: 'pred_hplc_metrics_1', prediction_record_id: hplcRecord.id, chromatography_type: 'hplc',
  input_snapshot: hplcRecord.input_snapshot,
  result: {
    task_type: 'hplc', schema_version: '1.0', rows: [{ candidate_id: 'ADH', separation: 1 }, { candidate_id: 'IA', separation: 1 }],
    metrics: [
      { key: 'ADH.compound_1.rt_q50_min', label: 'ADH compound 1 q50 retention time', value: 12.812872886657717, unit: 'min', origin: 'model' },
      { key: 'ADH.compound_2.rt_q50_min', label: 'ADH compound 2 q50 retention time', value: 14.43653392791748, unit: 'min', origin: 'model' },
      { key: 'IA.compound_1.rt_q50_min', label: 'IA compound 1 q50 retention time', value: 12.855504989624023, unit: 'min', origin: 'model' },
      { key: 'IA.compound_2.rt_q50_min', label: 'IA compound 2 q50 retention time', value: 8.83426570892334, unit: 'min', origin: 'model' },
      { key: 'recommended_column', label: 'Recommended column', value: 'ADH', unit: null, origin: 'derived' },
      { key: 'ADH.custom.long_metric_key_with_unknown_unit', value: null, unit: 'furlong', origin: 'model' },
    ],
    applicability: { supported_scope: 'HPLC enantioseparation', warnings: [] }, visualizations: [],
  },
} as unknown as ContractPrediction

const gcRecord = {
  ...record,
  id: 'record_gc_1', prediction_id: 'pred_gc_1', chromatography_type: 'gc',
  input_snapshot: { structure_refs: ['str_gc_1'], normalized_structures: ['CC'], input: { temperature_program: { mode: 'linear', initial_temperature_c: 50, final_temperature_c: 200, heating_rate_c_per_min: 10, initial_hold_min: 1 } } },
} as unknown as ContractPredictionRecord

const gcPrediction = {
  ...prediction,
  id: 'pred_gc_1', prediction_record_id: gcRecord.id, chromatography_type: 'gc',
  model: { id: 'gc-model', version: 'gc-v1', business_scope_id: 'gc-default', artifact_sha256: 'a'.repeat(64), input_schema_version: '1.0', output_schema_version: 'gc-retention-time-1', preprocessing_profile_id: null, preprocessing_profile_sha256: null },
  input_snapshot: gcRecord.input_snapshot,
  result: {
    task_type: 'gc', schema_version: 'gc-retention-time-1',
    rows: [{ retention_time_min: 12.5, quantiles: { q10: 10.1, q50: 12.5, q90: 14.8 } }],
    applicability: { supported_scope: 'GC selected-model scope', warnings: [{ code: 'GC_RESULT_WARNING', message: 'GC result warning' }] },
  },
  warnings: [{ code: 'GC_PREDICTION_WARNING', message: 'GC prediction warning' }],
} as unknown as ContractPrediction

const comparisonRecord = {
  ...record, id: 'record_tlc_compare', prediction_id: 'pred_tlc_compare',
  input_snapshot: { structure_refs: ['str_readonly_1'], normalized_structures: ['CCO'], input: { conditions: [
    { solvent_components: [{ solvent_id: 'hexane', fraction: 0.75 }, { solvent_id: 'ethyl_acetate', fraction: 0.25 }] },
    { solvent_components: [{ solvent_id: 'hexane', fraction: 0.25 }, { solvent_id: 'ethyl_acetate', fraction: 0.75 }] },
  ] } },
} as unknown as ContractPredictionRecord
const comparisonPrediction = {
  ...prediction, id: 'pred_tlc_compare', prediction_record_id: comparisonRecord.id, tool: 'condition_compare',
  input_snapshot: comparisonRecord.input_snapshot,
  result: { task_type: 'tlc_condition_comparison', schema_version: '1.0', structure_ref: 'str_readonly_1', model_id: 'tlc-model', model_version: 'v1.1', artifact_sha256: 'a'.repeat(64), results: [
    { mobile_phase: { hexane: 0.75, methanol: 0, diethyl_ether: 0, ethyl_acetate: 0.25, dichloromethane: 0 }, predicted_rf: 0.965412, condition_index: 0, component_predictions: { ann: 0.962539, lgb: 0.963644, xgb: 0.954727, random_forest: 0.983612 } },
    { mobile_phase: { hexane: 0.25, methanol: 0, diethyl_ether: 0, ethyl_acetate: 0.75, dichloromethane: 0 }, predicted_rf: 0.985164, condition_index: 1, component_predictions: { ann: 0.987348, lgb: 0.980646, xgb: 0.98099, random_forest: 0.989488 } },
  ], warnings: ['EXPERIMENTAL_CONFIRMATION_REQUIRED: TLC results are initial condition suggestions and require experimental confirmation'] },
} as unknown as ContractPrediction
const reverseRecord = {
  ...record, id: 'record_tlc_reverse', prediction_id: 'pred_tlc_reverse',
  input_snapshot: { structure_refs: ['str_readonly_1'], normalized_structures: ['CCO'], input: { target_rf_min: 0, target_rf_max: 1, candidate_system_ids: ['hexane/ethyl_acetate'], ratio_step: 0.4, max_candidates: 2 } },
} as unknown as ContractPredictionRecord
const reverseResult = { task_type: 'tlc_reverse_solvent' as const, schema_version: '1.0', structure_ref: 'str_readonly_1', model_id: 'tlc-model', model_version: 'v1.1', artifact_sha256: 'a'.repeat(64), candidates: [
    { source: 'model', system_id: 'hexane/ethyl_acetate', mobile_phase: { hexane: 0.6, ethyl_acetate: 0.4 }, predicted_rf: 0.982003 },
    { source: 'model', system_id: 'hexane/ethyl_acetate', mobile_phase: { hexane: 0.19999999999999996, ethyl_acetate: 0.8 }, predicted_rf: 0.985637 },
  ], evaluated: 2, failed: 0, target_reachable: true, warnings: ['NO_INDEPENDENT_GENERALIZATION_EVIDENCE: no independent labeled-set evidence is available; results do not establish independent scientific generalization'] }
const reversePrediction = {
  ...prediction, id: 'pred_tlc_reverse', prediction_record_id: reverseRecord.id, tool: 'reverse_solvent',
  input_snapshot: reverseRecord.input_snapshot,
  result: reverseResult,
} as unknown as ContractPrediction
const sfcRecord = { ...record, id: 'record_sfc_probability', prediction_id: 'pred_sfc_probability', chromatography_type: 'sfc' } as unknown as ContractPredictionRecord
const sfcResult = { task_type: 'sfc' as const, schema_version: '1.1', rows: [{ candidate_id: 'sfc-candidate-1', rank: 1, separation: 0.82, score_semantics: 'positive_class_probability', conditions: { column: 'Column A', stationary_phase: 'chiral', mode: 'SFC', length_mm: 150, inner_diameter_mm: 4.6, particle_size_um: 5, column_temperature_c: 30, flow_rate_ml_min: 3 } }], applicability: { supported_scope: 'SFC', warnings: [] } }
const sfcPrediction = {
  ...prediction, id: 'pred_sfc_probability', prediction_record_id: sfcRecord.id, chromatography_type: 'sfc', input_snapshot: sfcRecord.input_snapshot,
  result: sfcResult,
} as unknown as ContractPrediction

function envelope(data: unknown, status = 200) { return new Response(JSON.stringify({ data, request_id: 'records-test', warnings: [] }), { status, headers: { 'content-type': 'application/json' } }) }

describe('prediction record detail', () => {
  let requests: { url: string; method: string }[]
  let predictionFailure: boolean
  let predictionMismatch: boolean
  let predictionResponse: Promise<Response> | null
  let activeRecord: ContractPredictionRecord
  let activePrediction: ContractPrediction
  let replayResponse: Promise<Response> | null
  let sfcScopeOptions: Record<string, unknown> | null
  let sfcScopeReadFailure: boolean

  beforeEach(() => {
    requests = []; predictionFailure = false; predictionMismatch = false; predictionResponse = null; activeRecord = record; activePrediction = prediction; replayResponse = null
    sfcScopeOptions = null
    sfcScopeReadFailure = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url.includes('/sfc/conditions/options?') && sfcScopeReadFailure) throw new TypeError('controlled scope read failure')
      if (url.includes('/sfc/conditions/options?') && sfcScopeOptions) return envelope(sfcScopeOptions)
      if (url.endsWith('/system/capabilities')) return envelope({ build_id: 'universal-v1', modules: [] })
      if (url.endsWith('/system/version')) return envelope({ version: '1.0' })
      if (url.endsWith('/system/access-context')) return envelope({ project_selection: 'resolved', default_project_id: 'priority-local', accessible_projects: [{ id: 'priority-local', name: 'Priority' }], allowed_actions: ['*'], permissions: ['*'] })
      if (url.includes('/prediction-records?')) return new Response(JSON.stringify({ data: [activeRecord], page: { limit: 20, has_more: false, next_cursor: null }, request_id: 'records-list', warnings: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
      if (url.endsWith(`/prediction-records/${activeRecord.id}`)) return envelope(activeRecord)
      if (url.endsWith(`/predictions/${activePrediction.id}`)) return predictionResponse ?? (predictionFailure
        ? new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: 'Not found' }, request_id: 'records-prediction-error' }), { status: 404, headers: { 'content-type': 'application/json' } })
        : envelope(predictionMismatch ? { ...activePrediction, prediction_record_id: 'record-other' } : activePrediction))
      if (url.endsWith(`/prediction-records/${record.id}/replay-draft`) && method === 'POST') return replayResponse ?? envelope({
        id: 'draft-record-1', source_record_id: record.id, source_prediction_id: prediction.id, project_id: 'priority-local', chromatography_type: 'tlc', tool: 'single',
        structure_refs: [{ type: 'structure', id: 'str_readonly_1' }], structure_snapshots: [{ id: 'str_readonly_1', project_id: 'priority-local', canonical_smiles: 'CCO', stereochemistry_status: 'unspecified' }],
        input: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.5 }, { solvent_id: 'ethyl_acetate', fraction: 0.5 }] },
        schema_version: '1.0', model_selection: { mode: 'exact', model_id: 'tlc-model', model_version: 'v1.1' },
        source_model_snapshot: { id: 'tlc-model', version: 'v1.1', business_scope_id: 'tlc-default', artifact_sha256: 'a'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0' },
        source_record_revision: 2, revision: 1, etag: 'draft-etag-record', created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z',
      }, 201)
      throw new Error(`Unexpected GET ${url}`)
    }))
  })

  async function openPage() {
    const pinia = createPinia()
    const app = useAppStore(pinia)
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/data', name: 'data', component: PredictionRecordsView },
      { path: '/data/datasets', name: 'datasets', component: { template: '<div />' } },
      { path: '/prediction/:type', name: 'prediction', component: { template: '<div />' } },
      { path: '/prediction/:type/batch', name: 'batch-prediction', component: { template: '<div />' } },
    ] })
    await router.push('/data'); await router.isReady()
    const wrapper = mount(PredictionRecordsView, { global: { plugins: [pinia, router], stubs: { ConfirmationCard: true } } })
    await flushPromises()
    return { wrapper, router, app }
  }

  async function readDetail(wrapper: ReturnType<typeof mount>) {
    const button = wrapper.findAll('button').find((item) => item.text().includes('读取详情'))
    expect(button).toBeDefined()
    await button!.trigger('click'); await flushPromises()
  }

  it.each([true, false])('opens batch records using GET-only recovery when batch is assembled: %s', async (enabled) => {
    const tools = activeProfile.predictionTools as Record<string, readonly string[]>
    const previous = tools.tlc
    tools.tlc = [...(previous ?? []).filter((tool) => tool !== 'batch'), ...(enabled ? ['batch'] : [])]
    activeRecord = { ...record, batch_id: 'batch-record-1', result_ref: { type: 'batch_prediction', id: 'batch-record-1' } }
    const { wrapper, router } = await openPage()
    try {
      await readDetail(wrapper)
      expect(wrapper.find('[data-testid="replay-record"]').exists()).toBe(false)
      expect(requests.some((request) => request.url.endsWith(`/predictions/${prediction.id}`))).toBe(false)
      if (enabled) {
        await wrapper.get('[data-testid="open-batch-detail"]').trigger('click'); await flushPromises()
        expect(router.currentRoute.value.path).toBe('/prediction/tlc/batch')
        expect(router.currentRoute.value.query).toEqual({ batch_id: 'batch-record-1', project_id: 'priority-local' })
      } else {
        expect(wrapper.find('[data-testid="open-batch-detail"]').exists()).toBe(false)
        expect(wrapper.text()).toContain('当前构建未包含此色谱类型的批量详情')
      }
      expect(requests.every((request) => request.method === 'GET')).toBe(true)
    } finally {
      wrapper.unmount()
      tools.tlc = previous
    }
  })

  it('renders the linked contract snapshots, result, model provenance, and all applicability warnings', async () => {
    const { wrapper } = await openPage()
    const tableRegion = wrapper.get('[data-testid="records-table-scroll"]')
    expect(tableRegion.attributes('role')).toBe('region')
    expect(tableRegion.attributes('tabindex')).toBe('0')
    expect(tableRegion.attributes('aria-label')).toContain('可横向滚动')
    expect(tableRegion.findAll('th').map((item) => item.text())).toEqual(['', 'ID', '类型', '状态', 'revision', '操作'])
    await readDetail(wrapper)
    const text = wrapper.get('[data-testid="record-detail"]').text()
    expect(text).toContain('规范化 SMILES：CCO')
    expect(text).toContain('hexane 50.00%')
    expect(text).toContain('ethyl acetate 50.00%')
    expect(text).toContain('Rf')
    expect(text).toContain('0.22')
    expect(text).toContain('模型 ID')
    expect(text).toContain('模型版本')
    expect(text).toContain('a'.repeat(64))
    expect(text).toContain('RATIO_NORMALIZED')
    expect(text).toContain('EXPERIMENTAL_CONFIRMATION_REQUIRED')
    expect(text).toContain('NO_INDEPENDENT_GENERALIZATION_EVIDENCE')
    expect(text).not.toContain('private-actor')
    expect(text).not.toContain('private\\weights.bin')
    expect(requests.filter((item) => item.method === 'GET').map((item) => item.url)).toEqual(expect.arrayContaining([
      '/api/v1/prediction-records?project_id=priority-local', `/api/v1/prediction-records/${record.id}`, `/api/v1/predictions/${prediction.id}`,
    ]))
    expect(requests.some((item) => item.method !== 'GET')).toBe(false)
  })

  it('renders GC quantiles only when the historical result schema matches its own model snapshot', async () => {
    activeRecord = gcRecord; activePrediction = gcPrediction
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    const detail = wrapper.get('[data-testid="record-detail"]')
    const resultScroll = detail.get('[aria-label="正式结果表格，可横向滚动"]')
    expect(resultScroll.text()).toContain('保留时间')
    expect(resultScroll.text()).toContain('Q10')
    expect(resultScroll.text()).toContain('Q50')
    expect(resultScroll.text()).toContain('Q90')
    expect(resultScroll.text()).toContain('10.10min')
    expect(resultScroll.text()).toContain('12.50min')
    expect(resultScroll.text()).toContain('14.80min')
    expect(resultScroll.findAll('tbody tr').map((row) => row.text())).toEqual([
      '预测保留时间12.50min', 'Q1010.10min', 'Q5012.50min', 'Q9014.80min',
    ])
    expect(detail.text()).toContain('GC result warning')
    expect(requests.every((item) => item.method === 'GET')).toBe(true)
    wrapper.unmount()

    activeRecord = gcRecord
    activePrediction = {
      ...gcPrediction,
      model: { ...gcPrediction.model, output_schema_version: 'different-output' },
    } as ContractPrediction
    const invalid = await openPage()
    await readDetail(invalid.wrapper)
    expect(invalid.wrapper.get('[data-testid="gc-result-contract-error"]').text()).toContain('与该历史预测的模型输出 schema 不匹配')
    expect(invalid.wrapper.get('[data-testid="record-detail"]').text()).not.toContain('Q50')
    expect(invalid.wrapper.get('[data-testid="record-detail"]').text()).not.toContain('GC result warning')
    expect(invalid.wrapper.get('[data-testid="record-detail"]').text()).not.toContain('GC prediction warning')
    expect(invalid.wrapper.find('[aria-label="正式结果表格，可横向滚动"]').exists()).toBe(false)
    expect(requests.every((item) => item.method === 'GET')).toBe(true)
    invalid.wrapper.unmount()
  })

  it('renders TLC comparison input conditions, result rows, and optional warnings from the linked result', async () => {
    activeRecord = comparisonRecord; activePrediction = comparisonPrediction
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    const detail = wrapper.get('[data-testid="record-detail"]')
    expect(detail.text()).toContain('比较条件 1')
    expect(detail.text()).toContain('hexane 75.00%')
    expect(detail.text()).toContain('ethyl acetate 25.00%')
    const table = detail.get('[aria-label="正式结果表格，可横向滚动"]')
    expect(table.findAll('tbody tr')).toHaveLength(2)
    expect(table.text()).toContain('0.97')
    expect(table.text()).toContain('0.99')
    expect(detail.text()).toContain('EXPERIMENTAL_CONFIRMATION_REQUIRED')
    wrapper.unmount()

    activeRecord = comparisonRecord
    activePrediction = { ...comparisonPrediction, result: { ...comparisonPrediction.result, warnings: undefined } } as unknown as ContractPrediction
    const legacy = await openPage()
    await readDetail(legacy.wrapper)
    expect(legacy.wrapper.get('[aria-label="正式结果表格，可横向滚动"]').findAll('tbody tr')).toHaveLength(2)
    expect(legacy.wrapper.get('[data-testid="record-detail"]').text()).toContain('服务端未返回 warning')
    legacy.wrapper.unmount()
  })

  it('renders TLC reverse targets, candidate rows, counters, reachability, and warnings', async () => {
    activeRecord = reverseRecord; activePrediction = reversePrediction
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    const detail = wrapper.get('[data-testid="record-detail"]')
    expect(detail.text()).toContain('目标 Rf 范围')
    expect(detail.text()).toContain('0–1')
    expect(detail.text()).toContain('已评估')
    expect(detail.text()).toContain('2')
    expect(detail.text()).toContain('目标可达')
    expect(detail.text()).toContain('是')
    const table = detail.get('[aria-label="正式结果表格，可横向滚动"]')
    expect(table.findAll('tbody tr')).toHaveLength(2)
    expect(table.text()).toContain('0.98')
    expect(table.text()).toContain('0.99')
    expect(detail.text()).toContain('NO_INDEPENDENT_GENERALIZATION_EVIDENCE')
    wrapper.unmount()
  })

  it('reports malformed TLC result warnings without crashing or dropping record warnings', async () => {
    const reverseResultWithoutWarnings = Object.fromEntries(Object.entries(reverseResult).filter(([key]) => key !== 'warnings'))
    const cases: [ContractPredictionRecord, ContractPrediction][] = [
      [
        { ...comparisonRecord, warnings: ['record-level warning'] } as ContractPredictionRecord,
        { ...comparisonPrediction, result: { ...comparisonPrediction.result, warnings: {} } } as unknown as ContractPrediction,
      ],
      [
        { ...reverseRecord, warnings: ['record-level warning'] } as ContractPredictionRecord,
        { ...reversePrediction, result: reverseResultWithoutWarnings } as unknown as ContractPrediction,
      ],
      [
        { ...reverseRecord, warnings: ['record-level warning'] } as ContractPredictionRecord,
        { ...reversePrediction, result: { ...reversePrediction.result, warnings: ['valid warning', 42] } } as unknown as ContractPrediction,
      ],
    ]

    for (const [activeRecordFixture, activePredictionFixture] of cases) {
      activeRecord = activeRecordFixture
      activePrediction = activePredictionFixture
      const { wrapper } = await openPage()
      await readDetail(wrapper)
      const detail = wrapper.get('[data-testid="record-detail"]')
      expect(detail.get('[data-testid="record-result-warning-contract-error"]').text()).toContain('无法确认完整警告')
      expect(detail.text()).toContain('record-level warning')
      expect(detail.get('[aria-label="正式结果表格，可横向滚动"]').findAll('tbody tr')).toHaveLength(2)
      wrapper.unmount()
    }
  })

  function useCoupledSfcRecord() {
    const scope = {
      scope_id: 'scope-coupled-record', policy: { expansion_mode: 'coupled_discrete_conditions', policy_version: '1', candidate_source_sha256: 'd'.repeat(64) },
      candidate_count: 8, observed_count: 4, unobserved_count: 4,
    }
    const snapshot = { ...sfcRecord.input_snapshot, input: { candidate_scope_id: scope.scope_id, top_k: 3 } }
    activeRecord = { ...sfcRecord, input_snapshot: snapshot } as unknown as ContractPredictionRecord
    activePrediction = { ...sfcPrediction, input_snapshot: snapshot, result: {
      ...sfcResult, candidate_scope_provenance: scope, evaluated_candidate_count: 8,
      rows: [{ ...sfcResult.rows[0]!, conditions: { ...sfcResult.rows[0]!.conditions, observed_in_source: false } }],
    } } as unknown as ContractPrediction
    sfcScopeOptions = { project_id: 'priority-local', scope_id: scope.scope_id, model_id: activePrediction.model.id, model_version: activePrediction.model.version, candidate_count: 8, scope_provenance: scope }
    return scope
  }

  it('shows frozen, evaluated, returned and Top-k numbers separately after independent scope verification', async () => {
    useCoupledSfcRecord()
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    const counts = wrapper.get('[data-testid="sfc-record-counts"]')
    expect(counts.findAll('dt').map((item) => item.text())).toEqual(['冻结范围数量', '实际评估数量', '实际返回条数', '范围中目录已有组合', '范围中目录未见组合'])
    expect(counts.findAll('dd').map((item) => item.text())).toEqual(['8', '8', '1', '4', '4'])
    expect(wrapper.get('[data-testid="record-detail"]').text()).toContain('返回上限（Top-k）')
    const table = wrapper.get('[aria-label="正式结果表格，可横向滚动"]')
    expect(table.text()).toContain('条件目录中已有')
    expect(table.text()).toContain('否')
    expect(requests.filter((request) => request.url.includes('/sfc/conditions/options?'))).toHaveLength(1)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it('hides coupled rows and counts when the immutable source hash differs', async () => {
    const scope = useCoupledSfcRecord()
    sfcScopeOptions = { ...sfcScopeOptions!, scope_provenance: { ...scope, policy: { ...scope.policy, candidate_source_sha256: 'e'.repeat(64) } } }
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    expect(wrapper.get('[data-testid="sfc-result-contract-error"]').text()).toContain('不一致')
    expect(wrapper.find('[data-testid="sfc-record-counts"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="正式结果表格，可横向滚动"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps legacy scope counts unknown and does not query or invent provenance', async () => {
    activeRecord = sfcRecord; activePrediction = sfcPrediction
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    expect(wrapper.get('[data-testid="sfc-record-counts"]').text()).toContain('历史结果未记录')
    expect(requests.some((request) => request.url.includes('/sfc/conditions/options?'))).toBe(false)
    expect(wrapper.find('[data-testid="sfc-result-contract-error"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the input snapshot but hides unverified coupled output when its scope read fails', async () => {
    useCoupledSfcRecord(); sfcScopeReadFailure = true
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    expect(wrapper.get('[data-testid="sfc-result-contract-error"]').text()).toContain('核验失败')
    expect(wrapper.get('[data-testid="record-detail"]').text()).toContain('返回上限（Top-k）')
    expect(wrapper.find('[aria-label="正式结果表格，可横向滚动"]').exists()).toBe(false)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it('labels an explicitly declared SFC positive-class probability as probability', async () => {
    activeRecord = sfcRecord; activePrediction = sfcPrediction
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    const table = wrapper.get('[aria-label="正式结果表格，可横向滚动"]')
    expect(table.findAll('th').map((item) => item.text())).toContain('正类概率')
    expect(table.text()).not.toContain('分离度')
    wrapper.unmount()

    activeRecord = sfcRecord; activePrediction = { ...sfcPrediction, result: { ...sfcResult, rows: [{ ...sfcResult.rows[0], score_semantics: undefined }] } } as unknown as ContractPrediction
    const neutral = await openPage()
    await readDetail(neutral.wrapper)
    expect(neutral.wrapper.get('[aria-label="正式结果表格，可横向滚动"]').findAll('th').map((item) => item.text())).toContain('得分')
    neutral.wrapper.unmount()

    activePrediction = { ...sfcPrediction, result: { ...sfcResult, score_semantics: 'positive_class_probability', rows: [{ ...sfcResult.rows[0], score_semantics: undefined }] } } as unknown as ContractPrediction
    const inherited = await openPage()
    await readDetail(inherited.wrapper)
    expect(inherited.wrapper.get('[aria-label="正式结果表格，可横向滚动"]').findAll('th').map((item) => item.text())).toContain('正类概率')
    inherited.wrapper.unmount()

    activePrediction = { ...sfcPrediction, result: { ...sfcResult, score_semantics: 'positive_class_probability', rows: [{ ...sfcResult.rows[0], score_semantics: 'separation_probability' }] } } as unknown as ContractPrediction
    const conflict = await openPage()
    await readDetail(conflict.wrapper)
    expect(conflict.wrapper.get('[aria-label="正式结果表格，可横向滚动"]').findAll('th').map((item) => item.text())).toContain('得分')
    conflict.wrapper.unmount()
  })

  it('keeps result and warning empties hidden while the linked prediction GET is pending', async () => {
    activeRecord = comparisonRecord; activePrediction = comparisonPrediction
    let resolvePrediction!: (response: Response) => void
    predictionResponse = new Promise<Response>((resolve) => { resolvePrediction = resolve })
    const { wrapper } = await openPage()
    const button = wrapper.findAll('button').find((item) => item.text().includes('读取详情'))!
    await button.trigger('click')
    await vi.waitFor(() => expect(requests.some((item) => item.url.endsWith(`/predictions/${activePrediction.id}`))).toBe(true))
    await nextTick()
    const detail = wrapper.get('[data-testid="record-detail"]')
    expect(detail.text()).toContain('正在读取关联预测')
    expect(detail.text()).not.toContain('未关联可读取')
    expect(detail.text()).not.toContain('正式结果未返回')
    expect(detail.text()).not.toContain('服务端未返回 warning')
    expect(detail.text()).not.toContain('模型 ID')
    resolvePrediction(envelope(activePrediction))
    await flushPromises()
    expect(wrapper.get('[data-testid="record-detail"]').text()).toContain('0.97')
    wrapper.unmount()
  })

  it('fails closed when the linked prediction record ID does not match', async () => {
    predictionMismatch = true
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    const detail = wrapper.get('[data-testid="record-detail"]')
    expect(detail.text()).toContain('ID 不一致')
    expect(detail.text()).not.toContain('0.22')
    expect(detail.text()).toContain('关联预测警告当前不可确认')
    wrapper.unmount()
  })

  it('keeps a long record ID visible and allows the ID cell to wrap', async () => {
    const longId = `record_${'ab'.repeat(90)}`
    activeRecord = { ...record, id: longId, prediction_id: prediction.id } as ContractPredictionRecord
    activePrediction = { ...prediction, prediction_record_id: longId } as ContractPrediction
    const { wrapper } = await openPage()
    expect(wrapper.get('[data-testid="records-table-scroll"]').text()).toContain(longId)
    await readDetail(wrapper)
    expect(wrapper.get('[data-testid="record-detail"] h2').text()).toContain(longId)
    const viewSource = await import('node:fs').then(({ readFileSync }) => readFileSync('src/modules/records/PredictionRecordsView.vue', 'utf8'))
    expect(viewSource).toMatch(/\.records-table-wrap td:nth-child\(2\)[^}]*overflow-wrap:\s*anywhere/)
    wrapper.unmount()
  })

  it('keeps wide record and result tables inside horizontally scrollable containers', async () => {
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    const recordsScroll = wrapper.get('[data-testid="records-table-scroll"]')
    const resultScroll = wrapper.get('[data-testid="record-detail"] .table-wrap')
    expect(recordsScroll.element.tagName).toBe('DIV')
    expect(resultScroll.element.tagName).toBe('DIV')
    expect(resultScroll.attributes('role')).toBe('region')
    expect(resultScroll.attributes('tabindex')).toBe('0')
    expect(resultScroll.attributes('aria-label')).toContain('可横向滚动')
    expect(resultScroll.find('table').text()).toContain('0.22')
    const viewSource = await import('node:fs').then(({ readFileSync }) => readFileSync('src/modules/records/PredictionRecordsView.vue', 'utf8'))
    expect(viewSource).toMatch(/\.records-table-wrap[^}]*min-width:\s*0[^}]*overflow-x:\s*auto/)
    expect(viewSource).toMatch(/\.records-view[^}]*min-width:\s*0/)
    expect(viewSource).toMatch(/\.detail-block li\s*\{[^}]*min-width:\s*0[^}]*overflow-wrap:\s*anywhere/)
    expect(viewSource).toMatch(/\.detail-block li strong\s*\{[^}]*overflow-wrap:\s*anywhere/)
    expect(viewSource).toMatch(/\.records-table-wrap td:nth-child\(2\)[^}]*overflow-wrap:\s*anywhere/)
    expect(wrapper.get('[data-testid="records-table-scroll"]').text()).toContain(record.id)
  })

  it('reloads detail from the same GETs after a page refresh', async () => {
    const { wrapper: first } = await openPage()
    await readDetail(first)
    expect(first.get('[data-testid="record-detail"]').text()).toContain('0.22')
    first.unmount()

    const { wrapper: refreshed } = await openPage()
    expect(refreshed.find('[data-testid="record-detail"]').exists()).toBe(false)
    await readDetail(refreshed)
    expect(refreshed.get('[data-testid="record-detail"]').text()).toContain('0.22')
    expect(requests.filter((item) => item.url.endsWith(`/prediction-records/${record.id}`))).toHaveLength(2)
    expect(requests.filter((item) => item.url.endsWith(`/predictions/${prediction.id}`))).toHaveLength(2)
  })

  it('keeps record snapshots visible and states missing result truthfully when prediction GET fails', async () => {
    predictionFailure = true
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    const text = wrapper.get('[data-testid="record-detail"]').text()
    expect(text).toContain('关联预测 GET 失败')
    expect(text).toContain('hexane 50.00%')
    expect(text).not.toContain('0.22')
    expect(text).not.toContain('RATIO_NORMALIZED')
  })

  it('renders contract HPLC metrics with exact values, units, provenance, and safe fallbacks', async () => {
    activeRecord = hplcRecord; activePrediction = hplcPrediction
    const { wrapper } = await openPage()
    await readDetail(wrapper)

    const resultTable = wrapper.get('[aria-label="正式结果表格，可横向滚动"]')
    expect(resultTable.findAll('th').map((item) => item.text())).toContain('得分')
    expect(resultTable.text()).not.toContain('分离度')

    const metrics = wrapper.get('[data-testid="hplc-metrics-scroll"]')
    expect(metrics.attributes('role')).toBe('region')
    expect(metrics.attributes('tabindex')).toBe('0')
    expect(metrics.attributes('aria-label')).toContain('可横向滚动')
    const text = metrics.text()
    expect(text).toContain('ADH compound 1 q50 retention time')
    expect(text).toContain('ADH.compound_1.rt_q50_min')
    expect(text).toContain('12.81')
    expect(text).toContain('IA.compound_2.rt_q50_min')
    expect(text).toContain('8.83')
    expect(text).toContain('min')
    expect(text).toContain('model（模型返回）')
    expect(text).toContain('recommended_column')
    expect(text).toContain('ADH')
    expect(text).toContain('服务端未声明')
    expect(text).toContain('未知单位：furlong')
    expect(text).toContain('服务端未返回')
    expect(text).not.toContain('parity')
    expect(text).not.toContain('精度')
    expect(requests.every((item) => item.method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it('hides CC result rows with undeclared fields instead of displaying them as formal success', async () => {
    activeRecord = { ...record, id: 'record-cc-invalid', prediction_id: 'pred-cc-invalid', chromatography_type: 'cc' } as unknown as ContractPredictionRecord
    activePrediction = {
      ...prediction, id: 'pred-cc-invalid', prediction_record_id: activeRecord.id, chromatography_type: 'cc',
      result: { task_type: 'cc', schema_version: 'cc-v1', rows: [{ old_prediction: 0.91, absolute_path: 'C:/private/model.pth' }], applicability: { supported_scope: 'column chromatography' } },
    } as unknown as ContractPrediction
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    expect(wrapper.get('[data-testid="cc-result-contract-error"]').text()).toContain('已隐藏结果')
    expect(wrapper.find('[aria-label="正式结果表格，可横向滚动"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('C:/private/model.pth')
  })

  it('states when the HPLC result omits its optional metrics array', async () => {
    activeRecord = hplcRecord
    activePrediction = { ...hplcPrediction, result: { task_type: 'hplc', schema_version: '1.0', rows: [], applicability: { supported_scope: 'HPLC', warnings: [] }, visualizations: [] } } as unknown as ContractPrediction
    const { wrapper } = await openPage()
    await readDetail(wrapper)
    expect(wrapper.get('[data-testid="hplc-metrics-empty"]').text()).toContain('服务端未返回 HPLC 指标')
    expect(wrapper.find('[data-testid="hplc-metrics-scroll"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not report a stale replay POST error or navigate after project switch', async () => {
    const { wrapper, router, app } = await openPage()
    let rejectReplay!: (reason: Error) => void
    replayResponse = new Promise<Response>((_resolve, reject) => { rejectReplay = reject })
    await wrapper.get('[data-testid="replay-record-row"]').trigger('click')
    expect(requests.some((item) => item.method === 'POST' && item.url.endsWith('/replay-draft'))).toBe(true)

    app.accessContext = { ...app.accessContext, default_project_id: 'project-next', accessible_projects: [{ id: 'project-next', name: 'Next' }] } as never
    await nextTick()
    rejectReplay(new Error('late network failure'))
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/data')
    expect(wrapper.find('[data-testid="replay-record-error"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not let an in-flight replay POST navigate after the records page unmounts', async () => {
    const { wrapper, router } = await openPage()
    let resolveReplay!: (response: Response) => void
    replayResponse = new Promise<Response>((resolve) => { resolveReplay = resolve })
    const push = vi.spyOn(router, 'push')
    await wrapper.get('[data-testid="replay-record-row"]').trigger('click')
    wrapper.unmount()
    resolveReplay(envelope({
      id: 'draft-after-unmount', source_record_id: record.id, source_prediction_id: prediction.id, project_id: 'priority-local', chromatography_type: 'tlc', tool: 'single',
      structure_refs: [{ type: 'structure', id: 'str_readonly_1' }], input: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.5 }, { solvent_id: 'ethyl_acetate', fraction: 0.5 }] },
      structure_snapshots: [{ id: 'str_readonly_1', project_id: 'priority-local', canonical_smiles: 'CCO', stereochemistry_status: 'unspecified' }],
      schema_version: '1.0', model_selection: { mode: 'exact', model_id: 'tlc-model', model_version: 'v1.1' },
      source_model_snapshot: { id: 'tlc-model', version: 'v1.1', business_scope_id: 'tlc-default', artifact_sha256: 'a'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0' },
      source_record_revision: 2, revision: 1, etag: 'draft-etag-after-unmount', created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z',
    }, 201))
    await flushPromises()
    expect(push).not.toHaveBeenCalled()
  })
})
