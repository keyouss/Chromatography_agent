import { defineComponent, h, inject, onBeforeUnmount, ref, toRef } from 'vue'
import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Structure } from '@/core/types'
import { structureWorkspaceKey, type StructureTargetController } from '@/core/structure/workspace'
import { usePredictionStore } from '@/shared/prediction/store'
import { predictionWorkspaceKey } from '@/shared/prediction/workspace'
import { createSfc11ExactSelection, mapSfcModelOption, mapSfcToolChoice, optionsForSfcSearch, sfcScopeMatchesSelection, sfcSearchLimitsFromSchema, type Sfc11ModelOption, type Sfc11Scope } from '@/shared/prediction/sfc11'

vi.mock('@/app/generated/profile', () => ({
  activeProfile: { id: 'universal-v1', modules: ['prediction'], chromatographyTypes: ['sfc'], predictionTools: { sfc: ['single_column', 'full_column'] }, utilityFeatures: [], assistantModes: [] },
}))

import PredictionView from '@/modules/prediction/PredictionView.vue'

const projectId = 'priority-local'
const healthReady = { status: 'READY' as const, reason: null, checked_at: '2026-10-08T00:00:00Z' }
const keys = ['column', 'stationary_phase', 'mode', 'length_mm', 'inner_diameter_mm', 'particle_size_um', 'column_temperature_c', 'flow_rate_ml_min'] as const
const singleDomain = { model_scope: 'single_column', group_id: 'group-a', group_label: 'Group A', stationary_phase: 'amylose', mode: 'coated', allowed_columns: ['AD'] } as const
const fullDomain = { model_scope: 'all_column' } as const
const singleModel: Sfc11ModelOption = { id: 'sfc-single', version: 'single-v1', task_type: 'sfc', scope: 'sfc-business', input_schema_version: '1.1', output_schema_version: 'sfc-output-1.1', threshold: '1.2', sfc_domain: { ...singleDomain, allowed_columns: ['AD'] }, health: healthReady }
const fullModel: Sfc11ModelOption = { id: 'sfc-full', version: 'full-v1', task_type: 'sfc', scope: 'sfc-business', input_schema_version: '1.1', output_schema_version: 'sfc-output-1.1', threshold: '1.5', sfc_domain: fullDomain, health: healthReady }
const modelOptions = [singleModel, fullModel]
const tools = [
  { id: 'single_column', display_name: '单柱搜索', schema_version: '1.1', health: healthReady, limits: { max_candidates: 100, max_evaluations: 80, max_rows: 80 }, model_constraints: {} },
  { id: 'full_column', display_name: '全柱搜索', schema_version: '1.1', health: healthReady, limits: { max_candidates: 100, max_evaluations: 80, max_rows: 80 }, model_constraints: {} },
]
const inputSchema = {
  type: 'object', properties: {
    top_k: { type: 'integer', minimum: 1, maximum: 200 },
    modifier_components: { type: 'array', minItems: 1, maxItems: 20, items: { type: 'object', properties: { fraction: { type: 'number', minimum: 0, maximum: 1 } } } },
    modifier_total_fraction: { type: 'number', minimum: 0, maximum: 1 },
  },
}
const toolSchemas = Object.fromEntries(tools.map((tool) => [tool.id, {
  ...tool, input_schema: inputSchema, output_schema: {}, structure_count: { min: 2, max: 2, semantics: 'pair' }, units: {}, supported_visualizations: [],
}]))

const candidateValues: Record<(typeof keys)[number], (string | number)[]> = {
  column: ['AD'], stationary_phase: ['amylose'], mode: ['coated'], length_mm: [150], inner_diameter_mm: [4.6],
  particle_size_um: [5], column_temperature_c: [35], flow_rate_ml_min: [2.5],
}
let workspaceActorSequence = 0
let supportedExpansionModesOverride: unknown[] | undefined
let activeScopeMode: 'observed_distinct_rows' | 'coupled_discrete_conditions'
let activeScopeProvenance: Record<string, unknown> | null
let scopeOptionsProvenanceOverride: Record<string, unknown> | null | undefined
let resultProvenanceOverride: Record<string, unknown> | null | undefined
let predictionSuccess: boolean
let invalidCoupledPreviewCounts: boolean

function options(model: typeof singleModel | typeof fullModel, scopeId: string | null = null, count = 1, max = 100) {
  const domain = model.sfc_domain
  return {
    project_id: projectId, model_id: model.id, model_version: model.version, scope_id: scopeId,
    candidate_count: count, max_candidates: max, threshold: model.threshold, sfc_domain: domain,
    fields: keys.map((key) => ({ key, label: key, unit: key === 'flow_rate_ml_min' ? 'mL/min' : null, values: candidateValues[key] })),
    ...(supportedExpansionModesOverride === undefined ? {} : { supported_expansion_modes: supportedExpansionModesOverride }),
    ...(scopeId ? { scope_provenance: scopeOptionsProvenanceOverride === undefined ? activeScopeProvenance : scopeOptionsProvenanceOverride } : {}),
  }
}
function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, request_id: 'sfc-search-test', warnings: [] }), { status, headers: { 'content-type': 'application/json' } })
}

function sfcPrediction(status: 'RUNNING' | 'SUCCEEDED', overrides: Record<string, any> = {}) {
  const prediction: Record<string, any> = {
    id: 'accepted-sfc-1', job_id: 'job-sfc-1', project_id: projectId, prediction_record_id: 'record-sfc-1', chromatography_type: 'sfc',
    tool: 'full_column', schema_version: '1.1', status,
    model: { id: fullModel.id, version: fullModel.version, business_scope_id: fullModel.scope, artifact_sha256: 'b'.repeat(64), input_schema_version: '1.1', output_schema_version: fullModel.output_schema_version, threshold: fullModel.threshold },
    input_snapshot: { input: { candidate_scope_id: 'scope-sfc-1', top_k: 3 }, normalized_structures: [], structure_refs: [] },
    data_origin: 'model', warnings: [{ code: 'SFC_TEST_NOTICE', message: '合成测试警告' }], updated_at: '2026-10-08T00:00:00Z',
  }
  if (status === 'SUCCEEDED') prediction.result = {
    task_type: 'sfc', schema_version: fullModel.output_schema_version,
    applicability: { supported_scope: '合成测试范围', warnings: [{ code: 'SFC_SCOPE_NOTE', message: '适用范围以服务端返回为准' }] },
    rows: [{ candidate_id: 'synthetic-candidate-1', rank: 1, score_semantics: 'positive_class_probability', separation: 0.83,
      conditions: { column: 'AD', stationary_phase: 'amylose', mode: 'coated', length_mm: 150, inner_diameter_mm: 4.6, particle_size_um: 5, column_temperature_c: 35, flow_rate_ml_min: 2.5, observed_in_source: activeScopeMode === 'observed_distinct_rows' } }],
    evaluated_candidate_count: 1, candidate_scope_provenance: resultProvenanceOverride === undefined ? activeScopeProvenance : resultProvenanceOverride,
  }
  return { ...prediction, ...overrides }
}

function historicCoupledPrediction(provenance: Record<string, unknown>) {
  return {
    id: 'historic-sfc-1', project_id: projectId, chromatography_type: 'sfc', tool: 'single_column', schema_version: '1.1',
    status: 'SUCCEEDED', job_id: 'historic-job', model: { id: singleModel.id, version: singleModel.version, business_scope_id: singleModel.scope, artifact_sha256: 'c'.repeat(64), input_schema_version: '1.1', output_schema_version: singleModel.output_schema_version },
    input_snapshot: { input: { candidate_scope_id: 'historic-coupled-scope', top_k: 3 }, normalized_structures: [], structure_refs: [] },
    result: { task_type: 'sfc', schema_version: singleModel.output_schema_version, applicability: { supported_scope: '历史范围' }, rows: [], evaluated_candidate_count: 2, candidate_scope_provenance: provenance },
    data_origin: 'model', warnings: [], updated_at: '2026-10-08T00:00:00Z',
  }
}

let structureSequence = 0
let structureNormalizeCount = 0
let realStructureNormalizeCount = 0
let deferStructureNormalize = false
let pendingStructureNormalizations: Array<() => void> = []
const StructureInputStub = defineComponent({
  name: 'StructureInput',
  props: { projectId: { type: String, required: true }, disabled: { type: Boolean, default: false }, targetId: String, label: String, exampleSmiles: String },
  emits: ['normalized', 'invalidated', 'draft-change'],
  setup(props, { emit }) {
    const id = `sfc-structure-${++structureSequence}`
    const workspace = inject(structureWorkspaceKey, null)
    const smiles = ref(props.exampleSmiles ?? 'CC')
    const normalized = ref<Structure | null>(null)
    function invalidate() { normalized.value = null; emit('invalidated') }
    const controller: StructureTargetController = {
      id: props.targetId ?? id, label: props.label ?? '测试结构', projectId: props.projectId,
      disabled: toRef(props, 'disabled'), smiles, getSmiles: () => smiles.value,
      setSmiles(value) { smiles.value = value; invalidate(); emit('draft-change', value) },
      invalidate,
      async normalize() {
        structureNormalizeCount += 1
        const raw = smiles.value
        if (deferStructureNormalize) await new Promise<void>((resolve) => pendingStructureNormalizations.push(resolve))
        if (smiles.value !== raw || raw.trim().toUpperCase() === 'INVALID') return null
        const structure = { id: `${id}-normalized-${structureNormalizeCount}`, project_id: props.projectId, canonical_smiles: raw, stereochemistry_status: raw.includes('@') ? 'specified' : 'not_applicable' } as Structure
        normalized.value = structure
        emit('normalized', structure)
        return structure
      },
    }
    const unregister = workspace?.registerTarget(controller)
    onBeforeUnmount(() => unregister?.())
    function completeNormalization() { void controller.normalize() }
    return () => h('div', { 'data-target-id': props.targetId, 'data-example-smiles': props.exampleSmiles }, [
      h('input', { 'data-testid': `raw-${props.targetId ?? id}`, value: smiles.value, disabled: props.disabled, onInput: (event: Event) => controller.setSmiles((event.target as HTMLInputElement).value) }),
      h('button', { type: 'button', 'data-testid': `normalize-${id}`, 'data-label': props.label, 'data-example-smiles': props.exampleSmiles, disabled: props.disabled, onClick: completeNormalization }, '规范化测试结构'),
    ])
  },
})

describe('SFC 1.1 exact search integration', () => {
  let workspaceActorId: string
  let requests: Array<{ url: string; method: string; body?: Record<string, any>; headers: Record<string, string> }>
  let pendingModelOptions: ((response: Response) => void) | null
  let deferModelOptions: boolean
  let countScope: number
  let storage: Map<string, string>
  let capabilityOverride: Record<string, any> | null
  let oldPredictionResponse: Record<string, any> | null
  let predictionGetResponses: (Record<string, any> | Error)[] | null
  let predictionGetCount: number
  let deferPredictionGetResponse: boolean
  let pendingPredictionGetReject: ((reason: unknown) => void) | null
  let predictionMismatch: 'model' | 'version' | 'schema' | 'tool' | 'scope' | null
  let transportFailure: ((url: string, method: string) => Response | Error | null) | null

  beforeEach(() => {
    workspaceActorId = `actor-${++workspaceActorSequence}`
    requests = []; structureSequence = 0; structureNormalizeCount = 0; realStructureNormalizeCount = 0; deferStructureNormalize = false; pendingStructureNormalizations = []; pendingModelOptions = null; deferModelOptions = false; countScope = 1
    storage = new Map<string, string>(); capabilityOverride = null; oldPredictionResponse = null; predictionMismatch = 'model'; predictionGetResponses = null; predictionGetCount = 0
    deferPredictionGetResponse = false; pendingPredictionGetReject = null
    supportedExpansionModesOverride = undefined; activeScopeMode = 'observed_distinct_rows'; activeScopeProvenance = null
    scopeOptionsProvenanceOverride = undefined; resultProvenanceOverride = undefined; predictionSuccess = false; invalidCoupledPreviewCounts = false
    transportFailure = null
    vi.stubGlobal('localStorage', {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => storage.set(key, String(value)),
      removeItem: (key: string) => storage.delete(key),
      clear: () => storage.clear(),
    })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, any> : undefined
      const headers = init?.headers ? Object.fromEntries(new Headers(init.headers).entries()) : {}
      requests.push({ url, method, body, headers })
      const injectedFailure = transportFailure?.(url, method)
      if (injectedFailure instanceof Error) throw injectedFailure
      if (injectedFailure) return injectedFailure
      if (url.endsWith('/system/access-context')) return envelope({ actor: { id: workspaceActorId, auth_mode: 'deployment_default', policy_version: 'p1' }, actor_id: workspaceActorId, auth_mode: 'deployment_default', allowed_actions: ['*'], permissions: ['*'], is_multi_user_authenticated: false, project_selection: 'resolved', default_project_id: projectId, accessible_projects: [{ id: projectId, name: 'Priority' }] })
      if (url.endsWith('/system/capabilities')) return envelope({ build_id: 'universal-v1', capabilities: [] })
      if (url.endsWith('/system/version')) return envelope({ application_version: '1', build_id: 'universal-v1', api_schema_version: '1', migration_version: '001', dependency_lock_hash: 'test' })
      if (url.endsWith('/structures/normalize') && method === 'POST') {
        realStructureNormalizeCount += 1
        return envelope({ id: `real-normalized-${realStructureNormalizeCount}`, project_id: projectId, canonical_smiles: body?.value, stereochemistry_status: String(body?.value).includes('@') ? 'specified' : 'not_applicable' })
      }
      if (url.endsWith('/prediction/types')) return envelope([{ id: 'sfc', display_name: 'SFC', tools: ['single_column', 'full_column'], health: healthReady }])
      if (url.endsWith('/prediction/types/sfc')) return envelope(capabilityOverride ?? { id: 'sfc', display_name: 'SFC', tools, models: modelOptions, limits: { max_candidates: 100, max_evaluations: 80, max_rows: 80 }, health: healthReady })
      const schemaMatch = url.match(/\/prediction\/types\/sfc\/tools\/([^/]+)\/schema\?schema_version=1\.1$/)
      if (schemaMatch) return envelope(toolSchemas[decodeURIComponent(schemaMatch[1]) as keyof typeof toolSchemas])
      if (url.startsWith('/api/v1/sfc/conditions/options')) {
        const query = new URL(url, 'http://localhost').searchParams
        const model = modelOptions.find((item) => item.id === query.get('model_id') && item.version === query.get('model_version'))
          ?? (query.get('scope_id') === 'historic-coupled-scope' ? singleModel : undefined)
          ?? modelOptions.find((item) => item.id === [...requests].reverse().find((request) => request.url.endsWith('/sfc/candidate-scopes'))?.body?.model_selection?.model_id)
          ?? singleModel
        const scopeId = query.get('scope_id')
        if (deferModelOptions && !scopeId) return new Promise<Response>((resolve) => { pendingModelOptions = resolve })
        return envelope(options(model, scopeId, scopeId ? countScope : 1, 100))
      }
      if (url.endsWith('/sfc/candidate-scopes/preview') && method === 'POST') {
        const mode = body?.expansion_mode ?? 'observed_distinct_rows'
        const observed = mode === 'coupled_discrete_conditions' ? Math.min(1, countScope) : countScope
        const unobserved = mode === 'coupled_discrete_conditions' ? countScope - observed + (invalidCoupledPreviewCounts ? 1 : 0) : 0
        return envelope({ project_id: projectId, model_id: body?.model_selection?.model_id, model_version: body?.model_selection?.model_version,
          matched_count: countScope, max_candidates: body?.max_candidates, expansion_mode: mode,
          policy: mode === 'coupled_discrete_conditions' ? { expansion_mode: mode, policy_version: '1', candidate_source_sha256: 'f'.repeat(64) } : null,
          observed_count: observed, unobserved_count: unobserved,
          status: countScope === 0 ? 'EMPTY' : countScope > body?.max_candidates ? 'OVER_LIMIT' : 'READY' })
      }
      if (url.endsWith('/sfc/candidate-scopes') && method === 'POST') {
        const candidateCount = countScope
        activeScopeMode = body?.expansion_mode ?? 'observed_distinct_rows'
        activeScopeProvenance = activeScopeMode === 'coupled_discrete_conditions' ? {
          scope_id: 'scope-sfc-1', policy: { expansion_mode: activeScopeMode, policy_version: '1', candidate_source_sha256: 'f'.repeat(64) },
          candidate_count: candidateCount, observed_count: Math.min(1, candidateCount), unobserved_count: candidateCount - Math.min(1, candidateCount),
        } : null
        const condition = Object.fromEntries(Object.entries(body?.axes ?? {}).map(([key, values]) => [key, (values as unknown[])[0]]))
        return envelope({ id: 'scope-sfc-1', project_id: projectId, model_id: body?.model_selection?.model_id, model_version: body?.model_selection?.model_version,
          conditions: [condition], candidate_count: candidateCount, max_candidates: 100, truncated: candidateCount > 500,
          expansion: activeScopeMode, expansion_mode: activeScopeMode, policy: activeScopeProvenance?.policy ?? null,
          observed_count: activeScopeProvenance?.observed_count ?? candidateCount, unobserved_count: activeScopeProvenance?.unobserved_count ?? 0 })
      }
      if (url.endsWith('/predictions') && method === 'POST') return envelope({ id: 'accepted-sfc-1', job_id: 'job-sfc-1', prediction_record_id: 'record-sfc-1', resource_ref: { type: 'prediction', id: 'accepted-sfc-1' }, status: 'QUEUED' }, 202)
      if (url.endsWith('/predictions/accepted-sfc-1')) {
        if (deferPredictionGetResponse) {
          deferPredictionGetResponse = false
          predictionGetCount += 1
          return new Promise<Response>((_resolve, reject) => { pendingPredictionGetReject = reject })
        }
        if (predictionGetResponses) {
          const response = predictionGetResponses[Math.min(predictionGetCount, predictionGetResponses.length - 1)]!
          predictionGetCount += 1
          if (response instanceof Error) throw response
          return envelope(response)
        }
        if (predictionSuccess) return envelope(sfcPrediction('SUCCEEDED'))
        return envelope({
        id: 'accepted-sfc-1', job_id: 'job-sfc-1', project_id: projectId, prediction_record_id: 'record-sfc-1', chromatography_type: 'sfc',
        tool: predictionMismatch === 'tool' ? 'single_column' : 'full_column',
        schema_version: predictionMismatch === 'schema' ? 'sfc-v1.0' : '1.1', status: 'FAILED',
        model: { id: predictionMismatch === 'model' ? 'other-model' : fullModel.id, version: predictionMismatch === 'version' ? 'stale-v1' : fullModel.version, business_scope_id: fullModel.scope, artifact_sha256: 'b'.repeat(64), input_schema_version: '1.1', output_schema_version: fullModel.output_schema_version },
        input_snapshot: { input: { candidate_scope_id: predictionMismatch === 'scope' ? 'other-scope' : 'scope-sfc-1' }, normalized_structures: [], structure_refs: [] },
        data_origin: 'model', warnings: [], updated_at: '2026-10-08T00:00:00Z',
        })
      }
      if (url.endsWith('/predictions/historic-sfc-1') && oldPredictionResponse) return envelope(oldPredictionResponse)
      throw new Error(`Unexpected ${method} ${url}`)
    }))
  })

  async function open(useStructureInputStub = true, attachToBody = false, initialTool?: string) {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/prediction/sfc', component: PredictionView, meta: { chromatographicType: 'sfc' } }] })
    await router.push(initialTool ? { path: '/prediction/sfc', query: { tool: initialTool } } : '/prediction/sfc'); await router.isReady()
    const pinia = createPinia()
    const wrapper = mount(PredictionView, { ...(attachToBody ? { attachTo: document.body } : {}), global: { plugins: [pinia, router], stubs: { ...(useStructureInputStub ? { StructureInput: StructureInputStub } : {}), SharedKetcherCanvas: { template: '<iframe title="Ketcher 结构绘图器" data-testid="shared-ketcher-stub" />' } } } })
    await flushPromises()
    return { wrapper, pinia }
  }

  function saveAcceptedPrediction(tool: string, id: string) {
    const key = predictionWorkspaceKey(workspaceActorId, projectId, 'sfc', tool)
    storage.set(key, JSON.stringify({ version: 1, actor: workspaceActorId, project: projectId, type: 'sfc', tool,
      smiles: [], form: null, modelKey: '', submissionLocked: false, predictionId: id,
      accepted: { id, job_id: 'historic-job', status: 'QUEUED' } }))
  }

  async function openSavedPrediction(tool: string, id: string) {
    saveAcceptedPrediction(tool, id)
    const opened = await open()
    if (tool !== 'single_column') {
      await opened.wrapper.get('[data-testid="sfc-tool-select"]').setValue(tool)
      await flushPromises()
    }
    return opened
  }

  async function normalizePrimary(wrapper: ReturnType<typeof mount>) {
    const controls = wrapper.findAll('[data-testid^="normalize-sfc-structure-"]')
    expect(controls).toHaveLength(2)
    await controls[0]!.trigger('click'); await controls[1]!.trigger('click'); await flushPromises()
  }

  async function completeForm(wrapper: ReturnType<typeof mount>, toolId: string, model: typeof singleModel | typeof fullModel, normalizeModifier = true, realStructureInput = false) {
    const toolSelect = wrapper.get('[data-testid="sfc-tool-select"]')
    if ((toolSelect.element as HTMLSelectElement).value !== toolId) { await toolSelect.setValue(toolId); await flushPromises() }
    await wrapper.get('[data-testid="sfc-model-select"]').setValue(JSON.stringify([model.id, model.version]))
    await flushPromises()
    for (const key of keys) {
      if (key === 'column' && toolId === 'single_column') continue
      if (!(wrapper.get(`[data-testid="axis-${key}"]`).element as HTMLSelectElement).disabled) {
        await wrapper.get(`[data-testid="axis-${key}"]`).setValue('0')
        await wrapper.get(`[data-testid="axis-add-${key}"]`).trigger('click')
      }
    }
    if (toolId === 'single_column') await wrapper.get('[data-testid="sfc-column-select"]').setValue('AD')
    await wrapper.get('[data-testid="add-modifier"]').trigger('click')
    if (normalizeModifier) {
      const modifierNormalizer = wrapper.findAll('[data-testid^="normalize-sfc-structure-"]').at(-1)!
      await modifierNormalizer.trigger('click')
    } else if (realStructureInput) await wrapper.findAll('.structure-input textarea').at(-1)!.setValue('CCO')
    else await wrapper.find('[data-testid^="raw-modifier:"]').setValue('CCO')
    await wrapper.get('[data-testid^="modifier-fraction-"]').setValue('1')
    await wrapper.get('[data-testid="modifier-total-fraction"]').setValue('0.25')
    await wrapper.get('[data-testid="sfc-top-k"]').setValue('3')
    await flushPromises()
  }

  it('auto-selects the single exact READY model without remounting the form', async () => {
    const { wrapper } = await open()
    const modelSelect = wrapper.get('[data-testid="sfc-model-select"]')
    const formElement = wrapper.get('[data-testid="sfc-search-form"]').element
    expect((modelSelect.element as HTMLSelectElement).value).toBe(JSON.stringify([singleModel.id, singleModel.version]))
    expect(wrapper.findAll('iframe[title="Ketcher 结构绘图器"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-target-id="primary"]')).toHaveLength(1)
    expect(wrapper.find('[data-target-id="secondary"]').attributes('data-example-smiles')).toBe('C[C@@H](N)C(=O)O')
    await flushPromises()
    expect(wrapper.get('[data-testid="sfc-search-form"]').element).toBe(formElement)
    expect(requests.some((request) => request.url.includes('/sfc/conditions/options?')
      && request.url.includes(`model_id=${singleModel.id}`) && request.url.includes(`model_version=${singleModel.version}`))).toBe(true)
    await wrapper.get('[data-testid="add-modifier"]').trigger('click')
    expect(wrapper.findAll('iframe[title="Ketcher 结构绘图器"]')).toHaveLength(1)
    expect(wrapper.find('[data-target-id^="modifier:"]').attributes('data-example-smiles')).toBe('CCO')
  })

  it('restores a partial legal axis draft after remount without submitting', async () => {
    const { wrapper } = await open()
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('full_column')
    await flushPromises()
    await wrapper.get('[data-testid="sfc-model-select"]').setValue(JSON.stringify([fullModel.id, fullModel.version]))
    await flushPromises()
    await wrapper.get('[data-testid="axis-stationary_phase"]').setValue('0')
    await wrapper.get('[data-testid="axis-add-stationary_phase"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="axis-chips-stationary_phase"]').text()).toContain('amylose')
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()

    const restored = await open()
    await restored.wrapper.get('[data-testid="sfc-tool-select"]').setValue('full_column')
    await flushPromises()
    expect(restored.wrapper.get('[data-testid="axis-chips-stationary_phase"]').text()).toContain('amylose')
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    restored.wrapper.unmount()
  })

  it('maps exact single/full models sharing one business scope and submits single-column 1.1 with two refs', async () => {
    const { wrapper } = await open()
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('single_column')
    await flushPromises()
    expect((wrapper.get('[data-testid="sfc-model-select"]').element as HTMLSelectElement).value).toBe(JSON.stringify([singleModel.id, singleModel.version]))
    const singleModelOptions = wrapper.get('[data-testid="sfc-model-select"]').findAll('option')
    expect(singleModelOptions.some((option) => (option.element as HTMLOptionElement).value === JSON.stringify([singleModel.id, singleModel.version]))).toBe(true)
    expect(singleModelOptions.some((option) => (option.element as HTMLOptionElement).value === JSON.stringify([fullModel.id, fullModel.version]))).toBe(false)
    await normalizePrimary(wrapper)
    await completeForm(wrapper, 'single_column', singleModel)
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    expect(wrapper.find('[data-testid="sfc-search-validation"]').exists()).toBe(false)
    const optionsRequest = requests.find((request) => request.url.includes('/sfc/conditions/options') && request.url.includes('model_id=sfc-single'))
    expect(optionsRequest?.url).toContain('model_version=single-v1')
    expect(optionsRequest?.url).not.toContain('scope_id=')
    expect(optionsRequest?.url).toContain('model_id=sfc-single')
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await flushPromises()
    const scope = requests.find((request) => request.url.endsWith('/sfc/candidate-scopes'))
    expect(scope?.headers['idempotency-key']).toBeTruthy()
    expect(scope?.body).toMatchObject({ project_id: projectId, max_candidates: 80, model_selection: { mode: 'exact', model_id: singleModel.id, model_version: singleModel.version } })
    // The endpoint models an idempotent reuse of a scope originally stored with budget 100.
    // The current request budget is 80, but the actual candidate_count is 1 and the immutable stored budget stays 100.
    expect(scope?.body?.model_selection).not.toHaveProperty('preprocessing_profile_id')
    const post = requests.find((request) => request.url.endsWith('/predictions') && request.method === 'POST')
    expect(post?.headers['idempotency-key']).toBeTruthy()
    const primaryRefs = post?.body?.structure_refs as string[]
    expect(primaryRefs).toHaveLength(2)
    expect(new Set(primaryRefs).size).toBe(2)
    expect(post?.body).toMatchObject({ project_id: projectId, schema_version: '1.1', chromatography_type: 'sfc', tool: 'single_column', model_selection: { mode: 'exact', model_id: singleModel.id, model_version: singleModel.version }, input: { candidate_scope_id: 'scope-sfc-1', column_id: 'AD', modifier_components: [{ fraction: 1 }], modifier_total_fraction: 0.25, top_k: 3 } })
    expect((post?.body?.input?.modifier_components as Array<{ structure_ref: string }>)[0]?.structure_ref).not.toBe(primaryRefs[0])
    expect((post?.body?.input?.modifier_components as Array<{ structure_ref: string }>)[0]?.structure_ref).not.toBe(primaryRefs[1])
    const scopeBoundOptions = requests.find((request) => request.url.includes('/sfc/conditions/options') && request.url.includes('scope_id=scope-sfc-1'))
    expect(scopeBoundOptions?.url).not.toContain('model_id=')
    wrapper.unmount()
  })

  it('normalizes two primary structures and the Modifier on prediction submit without manual normalization', async () => {
    const { wrapper } = await open()
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('single_column')
    await flushPromises()
    await completeForm(wrapper, 'single_column', singleModel, false)
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    expect(structureNormalizeCount).toBe(0)

    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await flushPromises()

    expect(structureNormalizeCount).toBe(3)
    const post = requests.find((request) => request.url.endsWith('/predictions') && request.method === 'POST')
    expect(post?.body?.structure_refs).toHaveLength(2)
    expect((post?.body?.input?.modifier_components as Array<{ structure_ref: string }>)[0]?.structure_ref).toBeTruthy()
    wrapper.unmount()
  })

  it('uses real StructureInput components to auto-normalize both subjects and the raw Modifier on search', async () => {
    const { wrapper } = await open(false, true)
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('single_column')
    await flushPromises()
    await completeForm(wrapper, 'single_column', singleModel, false, true)
    const smilesInputs = wrapper.findAll('.structure-input textarea')
    expect(smilesInputs).toHaveLength(3)
    await smilesInputs[0]!.setValue('C[C@H](O)C')
    await smilesInputs[1]!.setValue('C[C@@H](O)C')
    await smilesInputs[2]!.setValue('CCO')
    expect(realStructureNormalizeCount).toBe(0)
    const submitButton = wrapper.get('[data-testid="sfc-search-submit"]')
    expect((submitButton.element as HTMLButtonElement).disabled).toBe(false)
    await submitButton.trigger('click')
    await flushPromises()

    expect(realStructureNormalizeCount).toBe(3)
    expect(requests.filter((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toHaveLength(1)
    const post = requests.find((request) => request.url.endsWith('/predictions') && request.method === 'POST')
    expect(post?.body?.structure_refs).toEqual(['real-normalized-1', 'real-normalized-2'])
    expect(post?.body?.input?.modifier_components).toEqual([{ structure_ref: 'real-normalized-3', fraction: 1 }])
    wrapper.unmount()
    document.body.innerHTML = ''
  })

  it('does not create a candidate scope or prediction when automatic structure normalization fails', async () => {
    const { wrapper } = await open()
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('single_column')
    await flushPromises()
    await completeForm(wrapper, 'single_column', singleModel, false)
    await wrapper.get('[data-testid="raw-primary"]').setValue('INVALID')

    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await flushPromises()

    expect(structureNormalizeCount).toBe(3)
    expect(requests.some((request) => request.url.endsWith('/sfc/candidate-scopes'))).toBe(false)
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    expect(wrapper.findAll('[role="alert"]').some((alert) => alert.text().includes('结构校验未通过'))).toBe(true)
    wrapper.unmount()
  })

  it('blocks a second submit during automatic normalization and discards results after an input edit', async () => {
    const { wrapper } = await open()
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('single_column')
    await flushPromises()
    await completeForm(wrapper, 'single_column', singleModel, false)
    deferStructureNormalize = true

    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    expect(structureNormalizeCount).toBe(3)
    await wrapper.get('[data-testid="raw-secondary"]').setValue('CCN')
    deferStructureNormalize = false
    pendingStructureNormalizations.splice(0).forEach((resolve) => resolve())
    await flushPromises()

    expect(requests.some((request) => request.url.endsWith('/sfc/candidate-scopes'))).toBe(false)
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('does not let an in-flight SFC normalization submit after the chromatography type changes', async () => {
    const { wrapper, pinia } = await open()
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('single_column')
    await flushPromises()
    await completeForm(wrapper, 'single_column', singleModel, false)
    deferStructureNormalize = true

    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    expect(structureNormalizeCount).toBe(3)
    usePredictionStore(pinia).selectedType = 'gc'
    await flushPromises()
    deferStructureNormalize = false
    pendingStructureNormalizations.splice(0).forEach((resolve) => resolve())
    await flushPromises()

    expect(requests.some((request) => request.url.endsWith('/sfc/candidate-scopes'))).toBe(false)
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('submits full-column without a model group or column_id and preserves two exact models under a shared business scope', async () => {
    const { wrapper, pinia } = await open()
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('full_column')
    await flushPromises()
    const fullModelOptions = wrapper.get('[data-testid="sfc-model-select"]').findAll('option')
    expect(fullModelOptions.some((option) => (option.element as HTMLOptionElement).value === JSON.stringify([fullModel.id, fullModel.version]))).toBe(true)
    expect(fullModelOptions.some((option) => (option.element as HTMLOptionElement).value === JSON.stringify([singleModel.id, singleModel.version]))).toBe(false)
    await normalizePrimary(wrapper)
    await completeForm(wrapper, 'full_column', fullModel)
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    expect(wrapper.find('[data-testid="sfc-search-validation"]').exists()).toBe(false)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await flushPromises()
    const scope = requests.find((request) => request.url.endsWith('/sfc/candidate-scopes'))
    expect(scope?.body?.model_selection).toMatchObject({ mode: 'exact', model_id: fullModel.id, model_version: fullModel.version })
    const post = requests.find((request) => request.url.endsWith('/predictions') && request.method === 'POST')
    expect(post?.body?.structure_refs).toHaveLength(2)
    expect(new Set(post?.body?.structure_refs as string[]).size).toBe(2)
    expect(post?.body).toMatchObject({ tool: 'full_column' })
    expect(post?.body?.input).not.toHaveProperty('column_id')
    expect(post?.body).not.toHaveProperty('group_id')
    expect(usePredictionStore(pinia).prediction).toBeNull()
    expect(wrapper.text()).toContain('SFC 结果与当前项目、模型、工具或候选范围不匹配')
    wrapper.unmount()
  })

  it.each(['version', 'schema', 'tool', 'scope'] as const)('hides a settled prediction when its immutable binding differs (%s)', async (mismatch) => {
    predictionMismatch = mismatch
    const { wrapper, pinia } = await open()
    await normalizePrimary(wrapper)
    await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await flushPromises()
    expect(requests.filter((request) => request.url.endsWith('/predictions/accepted-sfc-1'))).toHaveLength(1)
    expect(usePredictionStore(pinia).prediction).toBeNull()
    expect(wrapper.text()).toContain('SFC 结果与当前项目、模型、工具或候选范围不匹配')
    wrapper.unmount()
  })

  it('renders a valid synthetic SUCCEEDED result with probability semantics, snapshot threshold, and server warnings', async () => {
    const { wrapper, pinia } = await open()
    await normalizePrimary(wrapper)
    await completeForm(wrapper, 'full_column', fullModel)
    predictionGetResponses = [sfcPrediction('SUCCEEDED')]
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await flushPromises()

    expect(usePredictionStore(pinia).prediction?.status).toBe('SUCCEEDED')
    const resultText = wrapper.get('.result-panel').text()
    expect(resultText).toContain('P(Rs ≥ 1.5)')
    expect(resultText).toContain('0.83')
    const resultTable = wrapper.get('.result-panel .table-wrap').text()
    for (const header of ['候选编号', '排名', '色谱柱', '固定相', '模式', '柱长（mm）', '内径（mm）', '填料粒径（µm）', '柱温（°C）', '流速（mL/min）']) {
      expect(resultTable).toContain(header)
    }
    for (const value of ['synthetic-candidate-1', 'AD', 'amylose', 'coated', '150', '4.6', '5', '35', '2.5']) {
      expect(resultTable).toContain(value)
    }
    expect(resultTable).not.toContain('{"column"')
    expect(resultText).toContain('合成测试范围')
    expect(resultText).toContain('SFC_SCOPE_NOTE')
    expect(resultText).toContain('合成测试警告')
    expect(resultText).toContain(fullModel.version)
    wrapper.unmount()
  })

  it('renders two SFC candidates in server order with their distinct conditions and frozen threshold', async () => {
    countScope = 2
    const base = sfcPrediction('SUCCEEDED').result
    predictionGetResponses = [sfcPrediction('SUCCEEDED', {
      result: {
        ...base,
        evaluated_candidate_count: 2,
        rows: [
          { candidate_id: 'synthetic-candidate-first', rank: 1, score_semantics: 'positive_class_probability', separation: 0.83,
            conditions: { column: 'AD', stationary_phase: 'amylose', mode: 'coated', length_mm: 150, inner_diameter_mm: 4.6, particle_size_um: 5, column_temperature_c: 35, flow_rate_ml_min: 2.5 } },
          { candidate_id: 'synthetic-candidate-second', rank: 2, score_semantics: 'positive_class_probability', separation: 0.71,
            conditions: { column: 'CO', stationary_phase: 'cellulose', mode: 'immobilized', length_mm: 250, inner_diameter_mm: 3, particle_size_um: 3, column_temperature_c: 40, flow_rate_ml_min: 1.8 } },
        ],
      },
    })]
    const { wrapper, pinia } = await open()
    await normalizePrimary(wrapper)
    await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await flushPromises()

    const store = usePredictionStore(pinia)
    const currentModel = store.typeCapability?.models.find((model) => model.id === fullModel.id && model.version === fullModel.version)
    expect(currentModel).toBeDefined()
    if (currentModel) currentModel.threshold = '9.9'
    await flushPromises()
    const resultText = wrapper.get('.result-panel').text()
    expect(resultText).toContain('P(Rs ≥ 1.5)')
    expect(resultText).toContain(fullModel.version)
    expect(resultText).toContain('SFC_SCOPE_NOTE')
    const rows = wrapper.findAll('.result-panel tbody tr')
    expect(rows).toHaveLength(2)
    expect(rows.map((row) => row.findAll('td').map((cell) => cell.text()))).toEqual([
      ['synthetic-candidate-first', '1', '0.83', 'AD', 'amylose', 'coated', '150.00', '4.60', '5.00', '35.00', '2.50'],
      ['synthetic-candidate-second', '2', '0.71', 'CO', 'cellulose', 'immobilized', '250.00', '3.00', '3.00', '40.00', '1.80'],
    ])
    expect(wrapper.get('.result-panel .table-wrap').text()).not.toContain('{"column"')
    expect(usePredictionStore(pinia).prediction?.status).toBe('SUCCEEDED')
    wrapper.unmount()
  })

  it('shows a neutral empty SFC result when the valid SUCCEEDED rows array is empty', async () => {
    const base = sfcPrediction('SUCCEEDED').result
    predictionGetResponses = [sfcPrediction('SUCCEEDED', { result: { ...base, rows: [] } })]
    const { wrapper, pinia } = await open()
    await normalizePrimary(wrapper)
    await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await flushPromises()

    expect(usePredictionStore(pinia).prediction?.status).toBe('SUCCEEDED')
    expect(wrapper.get('[data-testid="sfc-empty-result"]').text()).toBe('服务端未返回候选结果。')
    expect(wrapper.find('.result-panel .table-wrap').exists()).toBe(false)
    const resultText = wrapper.get('.result-panel').text()
    expect(resultText).not.toContain('目标不可达')
    expect(resultText).toContain(fullModel.version)
    expect(resultText).toContain('SFC_SCOPE_NOTE')
    expect(resultText).toContain('合成测试警告')
    wrapper.unmount()
  })

  it('polls a RUNNING SFC prediction until a bound SUCCEEDED response is rendered', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      predictionGetResponses = [sfcPrediction('RUNNING'), sfcPrediction('SUCCEEDED')]
      const { wrapper, pinia } = await open()
      await normalizePrimary(wrapper)
      await completeForm(wrapper, 'full_column', fullModel)
      await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
      await flushPromises()

      expect(predictionGetCount).toBe(1)
      expect(usePredictionStore(pinia).prediction?.status).toBe('RUNNING')
      await vi.advanceTimersByTimeAsync(1500)
      await flushPromises()

      expect(predictionGetCount).toBe(2)
      expect(usePredictionStore(pinia).prediction?.status).toBe('SUCCEEDED')
      expect(wrapper.get('.result-panel').text()).toContain('P(Rs ≥ 1.5)')
      await vi.advanceTimersByTimeAsync(3000)
      expect(predictionGetCount).toBe(2)
      wrapper.unmount()
    } finally { vi.useRealTimers() }
  })

  it('clears the result and stops polling when a later response breaks the exact SFC model binding', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      predictionGetResponses = [sfcPrediction('RUNNING'), sfcPrediction('SUCCEEDED', { model: { ...sfcPrediction('SUCCEEDED').model, id: 'different-model' } })]
      const { wrapper, pinia } = await open()
      await normalizePrimary(wrapper)
      await completeForm(wrapper, 'full_column', fullModel)
      await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
      await flushPromises()
      expect(usePredictionStore(pinia).prediction?.status).toBe('RUNNING')

      await vi.advanceTimersByTimeAsync(1500)
      await flushPromises()
      expect(predictionGetCount).toBe(2)
      expect(usePredictionStore(pinia).prediction).toBeNull()
      expect(wrapper.text()).toContain('SFC 结果与当前项目、模型、工具或候选范围不匹配')
      await vi.advanceTimersByTimeAsync(3000)
      expect(predictionGetCount).toBe(2)
      wrapper.unmount()
    } finally { vi.useRealTimers() }
  })

  it('ignores a rejected poll from the prior tool binding after the user switches tools', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      predictionGetResponses = [sfcPrediction('RUNNING')]
      const { wrapper, pinia } = await open()
      await normalizePrimary(wrapper)
      await completeForm(wrapper, 'full_column', fullModel)
      await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
      await flushPromises()
      expect(predictionGetCount).toBe(1)
      expect(usePredictionStore(pinia).prediction?.status).toBe('RUNNING')

      deferPredictionGetResponse = true
      await vi.advanceTimersByTimeAsync(1500)
      await flushPromises()
      expect(predictionGetCount).toBe(2)
      expect(pendingPredictionGetReject).toBeTruthy()

      await wrapper.get('[data-testid="sfc-tool-select"]').setValue('single_column')
      await flushPromises()
      expect(usePredictionStore(pinia).prediction).toBeNull()
      pendingPredictionGetReject!(new Error('late poll from previous SFC binding'))
      await flushPromises()

      expect(wrapper.find('[role="alert"]').exists()).toBe(false)
      expect(usePredictionStore(pinia).prediction).toBeNull()
      await vi.advanceTimersByTimeAsync(3000)
      expect(predictionGetCount).toBe(2)
      wrapper.unmount()
    } finally { vi.useRealTimers() }
  })

  it('ignores late options from a prior model/tool binding and does not create scope', async () => {
    const { wrapper } = await open()
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('single_column')
    await flushPromises()
    deferModelOptions = true
    await wrapper.get('[data-testid="sfc-model-select"]').setValue(JSON.stringify([singleModel.id, singleModel.version]))
    await flushPromises()
    expect(pendingModelOptions).toBeTruthy()
    await wrapper.get('[data-testid="sfc-tool-select"]').setValue('full_column')
    await flushPromises()
    pendingModelOptions!(envelope(options(singleModel)))
    await flushPromises()
    expect(wrapper.find('[data-testid="sfc-search-validation"]').exists()).toBe(true)
    expect(requests.some((request) => request.url.endsWith('/sfc/candidate-scopes'))).toBe(false)
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('checks a server-truncated scope preview by actual count and keeps every returned axis within submitted values', () => {
    const scope = {
      id: 'scope-large', project_id: projectId, model_id: singleModel.id, model_version: singleModel.version,
      conditions: Array.from({ length: 500 }, () => ({ column: 'AD', stationary_phase: 'amylose', mode: 'coated', length_mm: 150, inner_diameter_mm: 4.6, particle_size_um: 5, column_temperature_c: 35, flow_rate_ml_min: 2.5 })),
      candidate_count: 501, max_candidates: 1000, truncated: true, expansion: 'server_cartesian_or_historical_distinct_rows',
    } as Sfc11Scope
    const axes = Object.fromEntries(keys.map((key) => [key, candidateValues[key]]))
    expect(sfcScopeMatchesSelection(scope, singleModel, projectId, axes)).toBe(true)
    expect(sfcScopeMatchesSelection({ ...scope, conditions: [{ ...scope.conditions[0]!, column: 'outside' }] }, singleModel, projectId, axes)).toBe(false)
  })

  it('rejects actual candidate counts above this request budget before prediction POST', async () => {
    countScope = 81
    const { wrapper } = await open()
    await normalizePrimary(wrapper)
    await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    await flushPromises()
    const preview = requests.find((request) => request.url.endsWith('/sfc/candidate-scopes/preview'))
    expect(preview?.body?.max_candidates).toBe(80)
    expect(requests.some((request) => request.url.endsWith('/sfc/candidate-scopes'))).toBe(false)
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    expect(wrapper.get('[data-testid="sfc-candidate-preview"]').text()).toContain('超过上限')
    wrapper.unmount()
  })

  it('previews EMPTY without creating a scope and keeps all form input', async () => {
    countScope = 0
    const { wrapper } = await open()
    await normalizePrimary(wrapper)
    await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-preview-button"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-testid="sfc-candidate-preview"]').text()).toContain('当前组合无匹配')
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(requests.some((item) => item.url.endsWith('/sfc/candidate-scopes'))).toBe(false)
    expect((wrapper.get('[data-testid="sfc-top-k"]').element as HTMLInputElement).value).toBe('3')
    expect(wrapper.get('[data-testid="axis-chips-column"]').text()).toContain('AD')
    wrapper.unmount()
  })

  it('keeps base options and input after scope 422 and displays one stage error', async () => {
    transportFailure = (url) => url.endsWith('/sfc/candidate-scopes') ? new Response(JSON.stringify({ error: { code: 'SFC_CANDIDATE_SCOPE_EMPTY', message: 'empty' } }), { status: 422, headers: { 'Content-Type': 'application/json' } }) : null
    const { wrapper } = await open()
    await normalizePrimary(wrapper); await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(wrapper.get('[data-testid="sfc-candidate-error"]').text()).toContain('无匹配')
    expect(wrapper.find('[data-testid="sfc-options-retry"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="axis-chips-column"]').text()).toContain('AD')
    expect((wrapper.get('[data-testid="modifier-total-fraction"]').element as HTMLInputElement).value).toBe('0.25')
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('retries scoped options by GET and reuses its created scope on the next explicit submission', async () => {
    transportFailure = (url) => url.includes('/sfc/conditions/options?') && url.includes('scope_id=') ? new TypeError('network') : null
    const { wrapper } = await open()
    await normalizePrimary(wrapper); await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(wrapper.find('[data-testid="sfc-scoped-options-retry"]').exists()).toBe(true)
    transportFailure = null
    await wrapper.get('[data-testid="sfc-scoped-options-retry"]').trigger('click'); await flushPromises()
    expect(requests.filter((item) => item.url.endsWith('/sfc/candidate-scopes'))).toHaveLength(1)
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(requests.filter((item) => item.url.endsWith('/sfc/candidate-scopes'))).toHaveLength(1)
    expect(requests.filter((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toHaveLength(1)
    wrapper.unmount()
  })

  it('switches to coupled mode without losing form input and binds policy counts through the result', async () => {
    supportedExpansionModesOverride = ['observed_distinct_rows', 'coupled_discrete_conditions']
    countScope = 4
    let failFirstScopedOptions = true
    transportFailure = (url) => {
      if (failFirstScopedOptions && url.includes('/sfc/conditions/options?') && url.includes('scope_id=')) {
        failFirstScopedOptions = false
        return new TypeError('temporary network failure')
      }
      return null
    }
    predictionSuccess = true
    const { wrapper } = await open()
    await normalizePrimary(wrapper); await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(requests.filter((item) => item.url.endsWith('/sfc/candidate-scopes') && item.method === 'POST')).toHaveLength(1)
    expect(wrapper.find('[data-testid="sfc-scoped-options-retry"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="axis-chips-column"]').text()).toContain('AD')
    expect((wrapper.get('[data-testid="modifier-total-fraction"]').element as HTMLInputElement).value).toBe('0.25')

    transportFailure = null
    await wrapper.get('[data-testid="sfc-expansion-mode"]').setValue('coupled_discrete_conditions')
    expect(wrapper.get('[data-testid="axis-chips-column"]').text()).toContain('AD')
    expect(wrapper.find('[data-testid="sfc-scoped-options-retry"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="sfc-candidate-preview"]').text()).not.toContain('范围可用')
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    const previews = requests.filter((item) => item.url.endsWith('/sfc/candidate-scopes/preview'))
    const scopes = requests.filter((item) => item.url.endsWith('/sfc/candidate-scopes') && item.method === 'POST')
    expect(previews.map((item) => item.body?.expansion_mode)).toEqual(['observed_distinct_rows', 'coupled_discrete_conditions'])
    expect(scopes.map((item) => item.body?.expansion_mode)).toEqual(['observed_distinct_rows', 'coupled_discrete_conditions'])
    expect(wrapper.get('[data-testid="sfc-result-counts"]').text()).toContain('冻结候选范围4')
    expect(wrapper.get('[data-testid="sfc-result-counts"]').text()).toContain('实际评分候选1')
    expect(wrapper.get('[data-testid="sfc-result-counts"]').text()).toContain('实际返回条数1')
    expect(wrapper.get('[data-testid="sfc-top-k-ceiling"]').text()).toContain('TopK 上限：3')
    expect(wrapper.get('.result-panel .table-wrap').text()).toContain('条件目录中已有')
    expect(wrapper.get('.result-panel .table-wrap').text()).toContain('否')
    expect(requests.filter((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toHaveLength(1)
    wrapper.unmount()
  })

  it('fails closed when coupled scope options or restored result provenance differs from the created scope', async () => {
    supportedExpansionModesOverride = ['observed_distinct_rows', 'coupled_discrete_conditions']
    countScope = 4
    scopeOptionsProvenanceOverride = { scope_id: 'scope-sfc-1', policy: { expansion_mode: 'coupled_discrete_conditions', policy_version: '1', candidate_source_sha256: 'e'.repeat(64) }, candidate_count: 4, observed_count: 1, unobserved_count: 3 }
    const first = await open()
    await normalizePrimary(first.wrapper); await completeForm(first.wrapper, 'full_column', fullModel)
    await first.wrapper.get('[data-testid="sfc-expansion-mode"]').setValue('coupled_discrete_conditions')
    await first.wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(first.wrapper.find('[data-testid="sfc-scoped-options-retry"]').exists()).toBe(true)
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    first.wrapper.unmount()

    scopeOptionsProvenanceOverride = undefined
    resultProvenanceOverride = { ...activeScopeProvenance, policy: { expansion_mode: 'coupled_discrete_conditions', policy_version: '1', candidate_source_sha256: 'e'.repeat(64) } }
    predictionSuccess = true
    const second = await open()
    await normalizePrimary(second.wrapper); await completeForm(second.wrapper, 'full_column', fullModel)
    await second.wrapper.get('[data-testid="sfc-expansion-mode"]').setValue('coupled_discrete_conditions')
    await second.wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(second.wrapper.get('[data-testid="sfc-prediction-status-error"]').text()).toContain('结果已隐藏')
    expect(second.wrapper.find('[data-testid="sfc-result-counts"]').exists()).toBe(false)
    second.wrapper.unmount()
  })

  it('rejects coupled preview counts that do not add up before creating a scope', async () => {
    supportedExpansionModesOverride = ['observed_distinct_rows', 'coupled_discrete_conditions']
    countScope = 4
    invalidCoupledPreviewCounts = true
    const { wrapper } = await open()
    await normalizePrimary(wrapper); await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-expansion-mode"]').setValue('coupled_discrete_conditions')
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(requests.filter((item) => item.url.endsWith('/sfc/candidate-scopes/preview'))).toHaveLength(1)
    expect(requests.some((item) => item.url.endsWith('/sfc/candidate-scopes') && item.method === 'POST')).toBe(false)
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    expect(wrapper.find('[data-testid="sfc-candidate-error"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('locks an unknown prediction submission without losing inputs or sending a second POST', async () => {
    transportFailure = (url, method) => url.endsWith('/predictions') && method === 'POST' ? new TypeError('lost response') : null
    const { wrapper } = await open()
    await normalizePrimary(wrapper); await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(wrapper.get('[data-testid="sfc-prediction-submit-error"]').text()).toContain('结果未确认')
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(requests.filter((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toHaveLength(1)
    expect(wrapper.find('[data-testid="sfc-options-retry"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('preserves the accepted ID when status GET fails and recovers only by reading that ID', async () => {
    transportFailure = (url) => url.endsWith('/predictions/accepted-sfc-1') ? new TypeError('read failure') : null
    const { wrapper } = await open()
    await normalizePrimary(wrapper); await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(wrapper.get('[data-testid="sfc-prediction-status-error"]').text()).toContain('accepted-sfc-1')
    expect(wrapper.get('[data-testid="sfc-prediction-status-error"]').text()).not.toContain('不匹配')
    await wrapper.get('[data-testid="sfc-prediction-status-retry"]').trigger('click'); await flushPromises()
    expect(requests.filter((item) => item.url.endsWith('/predictions/accepted-sfc-1'))).toHaveLength(2)
    expect(requests.filter((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toHaveLength(1)
    wrapper.unmount()
  })

  it.each([422, 503])('separates a known 4xx rejection from an unknown 5xx submission (%s)', async (status) => {
    transportFailure = (url, method) => url.endsWith('/predictions') && method === 'POST'
      ? new Response(JSON.stringify({ error: { code: 'TEST_FAILURE', message: 'controlled failure' } }), { status, headers: { 'Content-Type': 'application/json' } }) : null
    const { wrapper } = await open()
    await normalizePrimary(wrapper); await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    expect(wrapper.get('[data-testid="sfc-prediction-submit-error"]').text()).toContain(status === 422 ? '被拒绝' : '结果未确认')
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(status === 503)
    expect(wrapper.find('[data-testid="sfc-options-retry"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('fails closed on options project/model/version or unexpected axes without trusting stale metadata', () => {
    const toolChoice = mapSfcToolChoice(tools[0]!)!
    const limits = sfcSearchLimitsFromSchema(inputSchema, tools[0]!.limits)!
    const base = options(singleModel) as unknown as Parameters<typeof optionsForSfcSearch>[0]
    const check = (value: Parameters<typeof optionsForSfcSearch>[0]) => optionsForSfcSearch(value, singleModel, toolChoice, projectId, limits)
    expect(check(base)).not.toBeNull()
    expect(check({ ...base, project_id: 'other-project' })).toBeNull()
    expect(check({ ...base, model_id: fullModel.id })).toBeNull()
    expect(check({ ...base, model_version: 'stale-version' })).toBeNull()
    expect(check({ ...base, fields: [...base.fields, { key: 'unknown_axis', label: 'unknown', values: ['x'] }] })).toBeNull()
  })

  it('uses legacy observed mode only when supported modes are absent and rejects explicit invalid capability lists', () => {
    const toolChoice = mapSfcToolChoice(tools[0]!)!
    const limits = sfcSearchLimitsFromSchema(inputSchema, tools[0]!.limits)!
    const base = options(singleModel) as unknown as Parameters<typeof optionsForSfcSearch>[0]
    const map = (value: Parameters<typeof optionsForSfcSearch>[0]) => optionsForSfcSearch(value, singleModel, toolChoice, projectId, limits)
    expect(map(base)?.supportedExpansionModes).toEqual(['observed_distinct_rows'])
    expect(map({ ...base, supported_expansion_modes: ['coupled_discrete_conditions'] })).toBeNull()
    expect(map({ ...base, supported_expansion_modes: ['observed_distinct_rows', 'unknown'] as never })).toBeNull()
    expect(map({ ...base, supported_expansion_modes: ['observed_distinct_rows', 'coupled_discrete_conditions'] })?.supportedExpansionModes)
      .toEqual(['observed_distinct_rows', 'coupled_discrete_conditions'])
  })

  it('rejects invalid mutually-exclusive options selectors before transport', async () => {
    const pinia = createPinia()
    const store = usePredictionStore(pinia)
    const before = requests.length
    await expect(store.loadSfc11Options({ project_id: projectId, scope_id: 'scope', model_id: 'model', model_version: 'v' } as never)).rejects.toThrow('必须且只能')
    await expect(store.loadSfc11Options({ project_id: projectId, model_id: 'model' } as never)).rejects.toThrow('必须且只能')
    await expect(store.loadSfc11Options({ project_id: projectId, scope_id: '', model_id: 'model', model_version: 'v' } as never)).rejects.toThrow('必须且只能')
    await expect(store.loadSfc11Options({ project_id: projectId, scope_id: 'scope', model_id: '', model_version: '' } as never)).rejects.toThrow('必须且只能')
    expect(requests).toHaveLength(before)
  })

  it('keeps physical READY status but refuses old-schema tools and models for new SFC 1.1 requests', () => {
    const oldTool = mapSfcToolChoice({ id: 'single_column', schema_version: '1.0', health: healthReady })
    const oldModel = mapSfcModelOption({ ...singleModel, sfc_domain: { ...singleDomain, allowed_columns: ['AD'] }, input_schema_version: '1.0' } as never)
    expect(oldTool?.health).toBe('READY')
    expect(oldTool?.newRequestReady).toBe(false)
    expect(oldTool?.unavailableReason).toContain('历史读取')
    expect(oldModel?.health).toBe('READY')
    expect(oldModel?.newRequestReady).toBe(false)
    expect(() => createSfc11ExactSelection({ ...singleModel, sfc_domain: { ...singleDomain, allowed_columns: ['AD'] }, input_schema_version: '1.0' } as never)).toThrow('schema 不是 1.1')
  })

  it('keeps the selected full-column workspace and accepted result when the page is reloaded', async () => {
    saveAcceptedPrediction('full_column', 'historic-sfc-1')
    oldPredictionResponse = sfcPrediction('SUCCEEDED', { id: 'historic-sfc-1' })
    const first = await open()
    await first.wrapper.get('[data-testid="sfc-tool-select"]').setValue('full_column')
    await flushPromises()
    const tool = first.wrapper.vm.$router.currentRoute.value.query.tool
    expect(tool).toBe('full_column')
    first.wrapper.unmount()

    const restored = await open(true, false, String(tool))
    expect(restored.wrapper.get('[data-testid="sfc-tool-select"]').element).toHaveProperty('value', 'full_column')
    expect(usePredictionStore(restored.pinia).prediction).toMatchObject({ id: 'historic-sfc-1', tool: 'full_column' })
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    restored.wrapper.unmount()
  })

  it.each([false, true])('restores an observed scope count only from bound scope GET without submitting (mismatch=%s)', async (mismatch) => {
    countScope = 7
    oldPredictionResponse = historicCoupledPrediction({})
    delete oldPredictionResponse.result.candidate_scope_provenance
    if (mismatch) transportFailure = (url) => url.includes('scope_id=historic-coupled-scope')
      ? envelope({ ...options(singleModel, 'historic-coupled-scope', 7), model_version: 'other-version' }) : null
    const { wrapper, pinia } = await openSavedPrediction('single_column', 'historic-sfc-1')

    expect(usePredictionStore(pinia).prediction?.id).toBe('historic-sfc-1')
    expect(wrapper.get('[data-testid="sfc-result-counts"]').findAll('dd')[0]!.text()).toBe(mismatch ? '未知' : '7')
    expect(requests.some((request) => request.url.includes('scope_id=historic-coupled-scope') && request.method === 'GET')).toBe(true)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it('revalidates a saved coupled result against independently loaded scope options after reload', async () => {
    countScope = 4
    activeScopeProvenance = { scope_id: 'historic-coupled-scope', policy: { expansion_mode: 'coupled_discrete_conditions', policy_version: '1', candidate_source_sha256: 'a'.repeat(64) }, candidate_count: 4, observed_count: 1, unobserved_count: 3 }
    oldPredictionResponse = historicCoupledPrediction(activeScopeProvenance)
    saveAcceptedPrediction('single_column', 'historic-sfc-1')
    const { wrapper, pinia } = await openSavedPrediction('single_column', 'historic-sfc-1')
    expect(usePredictionStore(pinia).prediction?.id).toBe('historic-sfc-1')
    expect(wrapper.get('.result-panel').text()).toContain(singleModel.version)
    expect(requests.some((request) => request.url.includes('/sfc/conditions/options?') && request.url.includes('scope_id=historic-coupled-scope'))).toBe(true)
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it.each(['source-hash', 'candidate-count'] as const)('hides a saved coupled result when independently loaded scope provenance differs (%s)', async (mismatch) => {
    countScope = 4
    activeScopeProvenance = { scope_id: 'historic-coupled-scope', policy: { expansion_mode: 'coupled_discrete_conditions', policy_version: '1', candidate_source_sha256: 'a'.repeat(64) }, candidate_count: 4, observed_count: 1, unobserved_count: 3 }
    oldPredictionResponse = historicCoupledPrediction(activeScopeProvenance)
    saveAcceptedPrediction('single_column', 'historic-sfc-1')
    if (mismatch === 'source-hash') {
      scopeOptionsProvenanceOverride = { ...activeScopeProvenance, policy: { expansion_mode: 'coupled_discrete_conditions', policy_version: '1', candidate_source_sha256: 'b'.repeat(64) } }
    } else {
      countScope = 5
      scopeOptionsProvenanceOverride = { ...activeScopeProvenance, candidate_count: 5, observed_count: 2, unobserved_count: 3 }
    }
    const { wrapper, pinia } = await openSavedPrediction('single_column', 'historic-sfc-1')
    expect(usePredictionStore(pinia).prediction).toBeNull()
    expect(wrapper.get('[role="alert"]').text()).toContain('绑定不一致')
    expect(wrapper.get('.result-panel').text()).not.toContain(singleModel.version)
    expect(storage.get(predictionWorkspaceKey(workspaceActorId, projectId, 'sfc', 'single_column'))).toContain('historic-sfc-1')
    expect(requests.some((request) => request.url.includes('/sfc/conditions/options?') && request.url.includes('scope_id=historic-coupled-scope'))).toBe(true)
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('rejects partial SFC results whose scope policy differs from the prepared coupled scope', async () => {
    supportedExpansionModesOverride = ['observed_distinct_rows', 'coupled_discrete_conditions']
    countScope = 4
    predictionSuccess = true
    resultProvenanceOverride = { scope_id: 'scope-sfc-1', policy: { expansion_mode: 'coupled_discrete_conditions', policy_version: '1', candidate_source_sha256: 'e'.repeat(64) }, candidate_count: 4, observed_count: 1, unobserved_count: 3 }
    const { wrapper } = await open()
    await normalizePrimary(wrapper); await completeForm(wrapper, 'full_column', fullModel)
    await wrapper.get('[data-testid="sfc-expansion-mode"]').setValue('coupled_discrete_conditions')
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit'); await flushPromises()
    const partial = sfcPrediction('SUCCEEDED', { status: 'PARTIAL' })
    predictionGetResponses = [partial]
    await wrapper.get('[data-testid="sfc-prediction-status-retry"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-testid="sfc-prediction-status-error"]').text()).toContain('不匹配')
    expect(wrapper.find('[data-testid="sfc-result-counts"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(['UNAVAILABLE', 'DEGRADED', 'old-schema'] as const)('restores a saved SFC result without current READY 1.1 capability (%s)', async (scenario) => {
    saveAcceptedPrediction('single_column', 'historic-sfc-1')
    oldPredictionResponse = {
      id: 'historic-sfc-1', project_id: projectId, chromatography_type: 'sfc', tool: 'single_column', schema_version: '1.0',
      status: 'SUCCEEDED', job_id: 'historic-job', model: { id: 'legacy-sfc', version: 'legacy-v1', business_scope_id: 'sfc-business', artifact_sha256: 'c'.repeat(64), input_schema_version: '1.0', output_schema_version: 'sfc-output-1.0' },
      input_snapshot: { input: { candidate_scope_id: 'legacy-scope' }, normalized_structures: [], structure_refs: [] },
      result: { task_type: 'sfc', schema_version: 'sfc-output-1.0', applicability: { supported_scope: 'legacy' }, rows: [{ candidate_id: 'legacy-candidate', rank: 1, score_semantics: 'positive_class_probability', separation: 0.42, conditions: { column: 'AD', stationary_phase: 'amylose', mode: 'coated', length_mm: 150, inner_diameter_mm: 4.6, particle_size_um: 5, column_temperature_c: 35, flow_rate_ml_min: 2.5 } }] },
      data_origin: 'model', warnings: [], updated_at: '2026-10-08T00:00:00Z',
    }
    if (scenario === 'UNAVAILABLE' || scenario === 'DEGRADED') {
      capabilityOverride = {
        id: 'sfc', display_name: 'SFC', tools: scenario === 'UNAVAILABLE' ? [] : tools, models: scenario === 'UNAVAILABLE' ? [] : modelOptions,
        limits: { max_candidates: 100, max_evaluations: 80, max_rows: 80 },
        health: { ...healthReady, status: scenario, reason: scenario === 'UNAVAILABLE' ? 'MODEL_VERIFICATION_REQUIRED' : 'verification pending' },
      }
    } else {
      capabilityOverride = { id: 'sfc', display_name: 'SFC', tools: [{ ...tools[0], schema_version: '1.0' }], models: [{ ...singleModel, input_schema_version: '1.0' }], limits: { max_candidates: 100, max_evaluations: 80, max_rows: 80 }, health: healthReady }
    }
    const { wrapper, pinia } = await openSavedPrediction('single_column', 'historic-sfc-1')
    expect(usePredictionStore(pinia).prediction?.id).toBe('historic-sfc-1')
    expect(wrapper.get('.result-panel').text()).toContain('legacy-v1')
    expect(requests.some((request) => request.url.endsWith('/predictions/historic-sfc-1'))).toBe(true)
    expect(requests.some((request) => request.url.endsWith('/sfc/candidate-scopes') || (request.url.endsWith('/predictions') && request.method === 'POST'))).toBe(false)
    wrapper.unmount()
  })
})
