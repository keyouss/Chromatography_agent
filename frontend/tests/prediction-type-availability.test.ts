import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import type { ContractPrediction, ContractToolCapability } from '@/core/contract-types'
import { usePredictionStore } from '@/shared/prediction/store'
import { useAppStore } from '@/stores/app'
import { isHplc11RawPair } from '@/shared/prediction/hplc11'

vi.mock('@/app/generated/profile', () => ({
  activeProfile: {
    id: 'cc-v1', modules: ['prediction'], chromatographyTypes: ['cc', 'hplc'],
    predictionTools: {}, utilityFeatures: [], assistantModes: [],
  },
}))

import PredictionView from '@/modules/prediction/PredictionView.vue'

const health = (reason: string) => ({ status: 'UNAVAILABLE', reason, checked_at: '2026-10-03T00:00:00Z' })
const types = [
  { id: 'cc', display_name: 'CC', tools: ['single'], health: health('MODEL_VERIFICATION_REQUIRED') },
  { id: 'hplc', display_name: 'HPLC', tools: ['single'], health: health('MODEL_CONTRACT_INCOMPLETE') },
  { id: 'gc', display_name: 'GC', tools: ['single'], health: health('MODEL_VERIFICATION_REQUIRED') },
]

function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, request_id: 'prediction-types-test', warnings: [] }), { status, headers: { 'content-type': 'application/json' } })
}

describe('built prediction type availability', () => {
  let requests: { url: string; method: string; body?: string; headers?: Record<string, string> }[]
  let normalizationCount: number
  let accessContextData: unknown
  let accessContextResponse: Promise<Response> | null
  let hplcHealthReason: string
  let hplcHealthStatus: 'READY' | 'UNAVAILABLE'
  let hplcHasProfile: boolean
  let hplcProfileSha: string
  let hplcModelInputVersion: string
  let hplcReplayDraft: unknown
  let normalizationOriginalInputs: (string | null)[]
  let deferHplcCreate: boolean
  let pendingHplcCreates: Array<(response: Response) => void>
  let ccHealthStatus: 'READY' | 'UNAVAILABLE'
  let ccHasProfile: boolean
  let ccProfileSha: string
  let deferCcCapability: boolean
  let pendingCcCapabilities: Array<(response: Response) => void>
  let ccReplayDraft: unknown
  let deferNormalization: boolean
  let pendingNormalizations: Array<(response: Response) => void>
  let failPredictionTypes: boolean
  const hplcColumns = ['ADH', 'ODH', 'IC', 'IA', 'OJH', 'ASH', 'IC3', 'IE', 'ID', 'OD3', 'IB', 'AD', 'AD3', 'IF', 'OD', 'AS', 'OJ3', 'IG', 'AZ', 'IAH', 'OJ', 'ICH', 'OZ3', 'IF3', 'IAU']
  const ccInputSchema = {
    properties: { input: { properties: {
      column_spec_id: { type: 'string', enum: ['silica-4g'] },
      eluent_system_id: { type: 'string', enum: ['cc4g-pe-ea-000-100-v1'] },
      loading_solvent_id: { type: 'string', enum: ['cc4g-loading-dcm-v1'] },
      sample_mass_mg: { type: 'number', minimum: 30, maximum: 327.3, unit: 'mg' },
      loading_solvent_volume_ul: { type: 'number', minimum: 200, maximum: 2000, unit: 'uL' },
      flow_rate_ml_min: { type: 'number', minimum: 5, maximum: 10, unit: 'mL/min' },
    } } },
  }
  const ccConstraints = {
    column_spec_ids: ['silica-4g'], eluent_system_ids: ['cc4g-pe-ea-000-100-v1'], loading_solvent_ids: ['cc4g-loading-dcm-v1'],
    numeric_input_ranges: {
      sample_mass_mg: { minimum: 30, maximum: 327.3, unit: 'mg' },
      loading_solvent_volume_ul: { minimum: 200, maximum: 2000, unit: 'uL' },
      flow_rate_ml_min: { minimum: 5, maximum: 10, unit: 'mL/min' },
    },
  }

  beforeEach(() => {
    requests = []; normalizationCount = 0; accessContextData = { actor: { id: 'actor', auth_mode: 'deployment_default', policy_version: 'policy-1' }, actor_id: 'actor', auth_mode: 'deployment_default', allowed_actions: ['*'], permissions: ['*'], is_multi_user_authenticated: false, project_selection: 'resolved', default_project_id: 'priority-local', accessible_projects: [{ id: 'priority-local', name: 'Priority' }] }; accessContextResponse = null; hplcHealthReason = 'MODEL_CONTRACT_INCOMPLETE'; hplcHealthStatus = 'UNAVAILABLE'; hplcHasProfile = true; hplcProfileSha = 'd'.repeat(64); hplcModelInputVersion = '1.1'; hplcReplayDraft = null; normalizationOriginalInputs = []; deferHplcCreate = false; pendingHplcCreates = []; ccHealthStatus = 'UNAVAILABLE'; ccHasProfile = true; ccProfileSha = 'a'.repeat(64); deferCcCapability = false; pendingCcCapabilities = []; ccReplayDraft = null; deferNormalization = false; pendingNormalizations = []; failPredictionTypes = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      requests.push({ url, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? init.body : undefined, headers: init?.headers instanceof Headers ? Object.fromEntries(init.headers.entries()) : undefined })
      if (url.endsWith('/structures/normalize')) {
        const request = JSON.parse(String(init?.body)) as { project_id: string; value: string }
        normalizationCount += 1
        const result = envelope({ id: `structure-${normalizationCount}`, project_id: request.project_id, canonical_smiles: `canonical:${request.value}`, schema_version: '1.0', snapshot_hash: 'a'.repeat(64), created_at: '2026-10-07T00:00:00Z', is_valid: true, has_stereochemistry: false, stereochemistry_status: 'not_applicable', original_input: { input_type: 'smiles', value: normalizationOriginalInputs[normalizationCount - 1] ?? request.value }, stereochemistry_warnings: [] })
        return deferNormalization ? new Promise<Response>((resolve) => pendingNormalizations.push(resolve)) : result
      }
      if (url.endsWith('/system/capabilities')) return envelope({ build_id: 'cc-v1', capabilities: [] })
      if (url.endsWith('/system/version')) return envelope({ application_version: '1.0', build_id: 'cc-v1', api_schema_version: '1.0', migration_version: '1', dependency_lock_hash: 'test' })
      if (url.endsWith('/system/access-context')) return accessContextResponse ?? envelope(accessContextData)
      if (url.endsWith('/prediction/types')) { if (failPredictionTypes) throw new TypeError('Failed to fetch'); return envelope(types) }
       if (url.includes('/prediction-drafts/cc-draft')) return envelope(ccReplayDraft)
       if (url.includes('/prediction-drafts/hplc-draft')) return envelope(hplcReplayDraft)
      if (url.endsWith('/prediction/types/cc')) {
        if (deferCcCapability) return new Promise<Response>((resolve) => pendingCcCapabilities.push(resolve))
        return envelope({
          id: 'cc', display_name: 'CC',
          tools: ccHealthStatus === 'READY' ? [{ id: 'single', schema_version: '1.0', health: { status: 'READY', reason: null, checked_at: '' } }] : [],
          models: ccHealthStatus === 'READY' ? [{ id: 'cc-model', version: 'cc-qgeognn-1', task_type: 'cc', scope: 'single', ...(ccHasProfile ? { preprocessing_profile_id: 'cc-profile-1', preprocessing_profile_sha256: ccProfileSha } : {}), health: { status: 'READY', reason: null, checked_at: '' } }] : [],
          limits: {}, health: ccHealthStatus === 'READY' ? { status: 'READY', reason: null, checked_at: '' } : health('MODEL_VERIFICATION_REQUIRED'),
        })
      }
      if (url.includes('/prediction/types/cc/tools/single/schema')) return envelope({
        id: 'single', schema_version: '1.0', input_schema: ccInputSchema,
        output_schema: {}, structure_count: { min: 2, max: 2, semantics: 'pair' }, model_constraints: ccConstraints,
        units: {}, supported_visualizations: [], limits: {}, health: { status: 'READY', reason: null, checked_at: '' },
      })
       if (url.endsWith('/predictions') && init?.method === 'POST') {
         const body = JSON.parse(String(init.body)) as { chromatography_type?: string }
         if (body.chromatography_type === 'cc') return envelope({ id: 'accepted-cc', job_id: 'cc-job', status: 'QUEUED', project_id: 'priority-local' }, 202)
         if (body.chromatography_type === 'hplc') return deferHplcCreate
           ? new Promise<Response>((resolve) => pendingHplcCreates.push(resolve))
           : envelope({ id: 'accepted-hplc', job_id: 'hplc-job', prediction_record_id: 'hplc-record', resource_ref: { type: 'prediction', id: 'accepted-hplc' }, status: 'QUEUED' }, 202)
       }
      if (url.endsWith('/predictions/accepted-cc')) return envelope({ id: 'accepted-cc', project_id: 'priority-local', chromatography_type: 'cc', tool: 'single', status: 'QUEUED', model: { id: 'cc-model', version: 'cc-qgeognn-1' }, input_snapshot: { input: {}, normalized_structures: [], structure_refs: [] }, data_origin: 'model', updated_at: '2026-10-07T00:00:00Z', warnings: [] })
      if (url.endsWith('/prediction/types/hplc')) return envelope({
        id: 'hplc', display_name: 'HPLC', tools: hplcHealthStatus === 'READY' ? [{ id: 'single', schema_version: '1.0' }] : [],
         models: hplcHealthStatus === 'READY' ? [{ id: 'hplc-model', version: 'v1', input_schema_version: hplcModelInputVersion, output_schema_version: '1.1', task_type: 'hplc', scope: 'single', ...(hplcHasProfile ? { preprocessing_profile_id: 'hplc-profile-current', preprocessing_profile_sha256: hplcProfileSha } : {}), health: { status: 'READY', reason: null, checked_at: '' } }] : [],
        limits: {}, health: hplcHealthStatus === 'READY' ? { status: 'READY', reason: null, checked_at: '' } : health(hplcHealthReason),
      })
      if (url.includes('/prediction/types/hplc/tools/single/schema')) return envelope({
        id: 'single', schema_version: '1.0', input_schema: { properties: { input: { properties: { column_ids: { type: 'array', items: { type: 'string' } } } } } },
        output_schema: {}, structure_count: { min: 2, max: 2, semantics: 'pair' }, model_constraints: { columns: hplcColumns }, units: {},
        supported_visualizations: [], limits: {}, health: { status: 'READY', reason: null, checked_at: '' },
      })
       if (url.endsWith('/predictions/accepted-hplc')) return envelope({ id: 'accepted-hplc', project_id: 'priority-local', chromatography_type: 'hplc', tool: 'single', status: 'FAILED', model: { id: 'hplc-model', version: 'v1', artifact_sha256: 'a'.repeat(64), business_scope_id: 'hplc', input_schema_version: '1.1', output_schema_version: '1.1' }, input_snapshot: { input: { column_ids: [hplcColumns[0]], flow_rate_ml_min: 1, isopropanol_fraction: 0.2, model_input_smiles: ['raw-1', 'raw-2'] }, normalized_structures: ['canonical-1', 'canonical-2'], structure_refs: ['structure-1', 'structure-2'] }, data_origin: 'production', updated_at: '2026-10-07T00:00:00Z', warnings: [] })
      throw new Error(`Unexpected request ${url}`)
    }))
  })

  async function openPage(path = '/prediction/cc', seedHplcColumns = false) {
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/prediction', component: PredictionView },
      { path: '/prediction/cc', component: PredictionView, meta: { chromatographicType: 'cc' } },
      { path: '/prediction/hplc', component: PredictionView, meta: { chromatographicType: 'hplc' } },
    ] })
    const pinia = createPinia()
    if (seedHplcColumns) usePredictionStore(pinia).toolSchema = {
      id: 'single', schema_version: '1.0', input_schema: {}, output_schema: {},
      structure_count: { min: 2, max: 2, semantics: 'pair' },
      model_constraints: { columns: ['ADH', 'IA'] }, units: {}, supported_visualizations: [], limits: { max_candidates: 25, max_evaluations: 25, max_rows: 25 },
      health: { status: 'READY', reason: null, checked_at: '' },
    } as ContractToolCapability
    await router.push(path); await router.isReady()
    const wrapper = mount(PredictionView, { global: { plugins: [pinia, router] } })
    await flushPromises()
    return { wrapper, router, pinia }
  }

  async function normalizeStructureSlot(wrapper: ReturnType<typeof mount>, index: number, value: string) {
    const slot = wrapper.findAll('.structure-input')[index]
    if (!slot) throw new Error(`Missing structure slot ${index}`)
    await slot.get('textarea').setValue(value)
    await slot.get('.primary').trigger('click')
    await flushPromises()
  }

  it('shows built types with unavailable reasons, excludes types outside the profile, and never submits', async () => {
    const { wrapper, router } = await openPage()
    const workspace = wrapper.get('.prediction-main-grid')
    expect(Array.from(workspace.element.children).map((element) => element.className)).toEqual(['prediction-input-column', 'panel result-panel'])
    const projectSection = wrapper.get('.project-section').element as HTMLElement
    expect(projectSection.compareDocumentPosition(workspace.element as HTMLElement) & 4).toBe(4)
    expect(wrapper.find('.prediction-input-column .empty-state').text()).toContain('MODEL_VERIFICATION_REQUIRED')
    expect(wrapper.find('.prediction-main-grid > .result-panel').text()).toContain('不会展示演示成功数据')
    const tabs = () => wrapper.findAll('[role="tab"]').map((tab) => tab.text())
    expect(tabs()).toEqual(['CC', 'HPLC'])
    expect(wrapper.text()).toContain('MODEL_VERIFICATION_REQUIRED')
    expect(wrapper.text()).toContain('提交已禁用')
    expect(wrapper.findAll('button').some((button) => button.text().includes('GC'))).toBe(false)

    await wrapper.get('[role="tab"]:nth-child(2)').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/prediction/hplc')
    expect(wrapper.text()).toContain('MODEL_CONTRACT_INCOMPLETE')
    expect(wrapper.text()).toContain('提交已禁用')
    expect(requests.some((request) => request.method !== 'GET')).toBe(false)
  })

  it('keeps profile navigation and a local SMILES preview when prediction metadata is offline', async () => {
    failPredictionTypes = true
    const { wrapper } = await openPage('/prediction/cc')
    const tabs = wrapper.findAll('[role="tab"]')
    expect(tabs.map((tab) => tab.text())).toEqual(['柱层析', '手性 HPLC'])
    expect(wrapper.find('[data-testid="prediction-offline-form"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prediction-offline-schema"]').text()).toContain('均未知')
    await wrapper.get('[data-testid="prediction-offline-smiles"]').setValue('CCO')
    expect(requests.some((request) => request.url.endsWith('/structures/normalize') || request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('shows a loading state before access context resolves and blocks only after an explicit empty result', async () => {
    let resolveAccessContext!: (response: Response) => void
    accessContextResponse = new Promise((resolve) => { resolveAccessContext = resolve })
    const pending = await openPage()
    expect(pending.wrapper.text()).toContain('正在读取访问上下文')
    expect(pending.wrapper.text()).not.toContain('没有为当前访问上下文分配可访问项目')

    resolveAccessContext(envelope({ actor: { id: 'actor', auth_mode: 'deployment_default', policy_version: 'policy-1' }, actor_id: 'actor', auth_mode: 'deployment_default', allowed_actions: ['*'], permissions: ['*'], is_multi_user_authenticated: false, project_selection: 'resolved', default_project_id: 'priority-local', accessible_projects: [{ id: 'priority-local', name: 'Priority' }] }))
    await flushPromises()
    expect(pending.wrapper.find('[data-testid="prediction-project"]').exists()).toBe(true)
    pending.wrapper.unmount()

    accessContextResponse = null
    accessContextData = { project_selection: 'no_accessible_projects', default_project_id: null, accessible_projects: [] }
    const blocked = await openPage()
    expect(blocked.wrapper.text()).toContain('服务端没有为当前访问上下文分配可访问项目')
    expect(blocked.wrapper.text()).not.toContain('正在读取访问上下文')
    expect(blocked.wrapper.find('[data-testid="prediction-project"]').exists()).toBe(false)
    expect(requests.some((request) => request.method !== 'GET')).toBe(false)
  })

  it('keeps a seeded HPLC column list from bypassing MODEL_VERIFICATION_REQUIRED', async () => {
    hplcHealthReason = 'MODEL_VERIFICATION_REQUIRED'
    const { wrapper } = await openPage('/prediction/hplc', true)
    expect(wrapper.text()).toContain('MODEL_VERIFICATION_REQUIRED')
    expect(wrapper.text()).toContain('提交已禁用')
    expect(wrapper.find('.prediction-form').exists()).toBe(false)
    expect(wrapper.findAll('button').some((button) => button.text().includes('开始 HPLC 预测'))).toBe(false)
    expect(requests.some((request) => request.method !== 'GET')).toBe(false)
    wrapper.unmount()
  })

  it('renders the exact 25 tool-capability columns only when HPLC is READY', async () => {
    hplcHealthStatus = 'READY'
    const { wrapper } = await openPage('/prediction/hplc')
    for (const [index, slot] of wrapper.findAll('.structure-input').entries()) {
      await slot.get('textarea').setValue(index === 0 ? 'C' : 'CC')
      await slot.get('.primary').trigger('click')
      await flushPromises()
    }
    const form = wrapper.get('.prediction-form')
    const columnSelect = form.get('select[multiple]')
    expect(columnSelect.findAll('option').map((option) => option.element.getAttribute('value'))).toEqual(hplcColumns)
    expect(form.findAll('button').some((button) => button.text().includes('开始 HPLC 预测'))).toBe(true)
    expect(requests.some((request) => request.method !== 'GET' && !request.url.endsWith('/structures/normalize'))).toBe(false)
    wrapper.unmount()
  })

  it('keeps HPLC enabled for two non-empty raw inputs and validates edited input only on submit', async () => {
    hplcHealthStatus = 'READY'
    const { wrapper } = await openPage('/prediction/hplc')
    const submit = () => wrapper.get('[data-testid="hplc-submit"]')
    expect((submit().element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('.prediction-form select[multiple]').setValue([hplcColumns[0]])

    const rawPair = [' C[C@H](O)C ', 'C[C@@H](O)C'] as const
    await wrapper.findAll('.structure-input')[0]!.get('textarea').setValue(rawPair[0])
    expect((submit().element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.findAll('.structure-input')[1]!.get('textarea').setValue(rawPair[1])
    expect((submit().element as HTMLButtonElement).disabled).toBe(false)
    expect(normalizationCount).toBe(0)
    expect(requests.some((request) => request.method === 'POST')).toBe(false)

    await normalizeStructureSlot(wrapper, 0, rawPair[0])
    await normalizeStructureSlot(wrapper, 1, rawPair[1])

    const editedRaw = 'C[C@H](N)C'
    await wrapper.findAll('.structure-input')[0]!.get('textarea').setValue(editedRaw)
    await nextTick()
    expect((submit().element as HTMLButtonElement).disabled).toBe(false)
    expect(normalizationCount).toBe(2)
    expect(requests.some((request) => request.method === 'POST' && request.url.endsWith('/predictions'))).toBe(false)

    await submit().trigger('click')
    await flushPromises()
    expect(normalizationCount).toBe(3)
    const create = requests.find((request) => request.method === 'POST' && request.url.endsWith('/predictions'))
    expect(JSON.parse(create?.body ?? '{}')).toMatchObject({ input: { model_input_smiles: [editedRaw, rawPair[1]] } })
    wrapper.unmount()
  })

  it('ignores a pending HPLC normalization after switching type and reloading HPLC', async () => {
    hplcHealthStatus = 'READY'
    const { wrapper } = await openPage('/prediction/hplc')
    await wrapper.get('.prediction-form select[multiple]').setValue([hplcColumns[0]])
    await normalizeStructureSlot(wrapper, 0, 'C[C@H](O)C')
    await normalizeStructureSlot(wrapper, 1, 'C[C@@H](O)C')
    expect((wrapper.get('[data-testid="hplc-submit"]').element as HTMLButtonElement).disabled).toBe(false)

    deferNormalization = true
    const firstSlot = wrapper.findAll('.structure-input')[0]!
    await firstSlot.get('textarea').setValue('C[C@H](N)C')
    await firstSlot.get('.primary').trigger('click')
    expect(pendingNormalizations).toHaveLength(1)

    await wrapper.findAll('[role="tab"]').find((tab) => tab.text() === 'CC')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('[role="tab"]').find((tab) => tab.text() === 'HPLC')!.trigger('click')
    await flushPromises()
    deferNormalization = false
    pendingNormalizations.shift()!(envelope({ id: 'late-hplc-structure', project_id: 'priority-local', canonical_smiles: 'late', schema_version: '1.0', snapshot_hash: 'a'.repeat(64), created_at: '2026-10-07T00:00:00Z', is_valid: true, has_stereochemistry: false, stereochemistry_status: 'not_applicable', original_input: { input_type: 'smiles', value: 'C[C@H](N)C' }, stereochemistry_warnings: [] }))
    await flushPromises()

    await wrapper.get('.prediction-form select[multiple]').setValue([hplcColumns[0]])
    await normalizeStructureSlot(wrapper, 1, 'C[C@@H](O)C')
    expect((wrapper.get('[data-testid="hplc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await normalizeStructureSlot(wrapper, 0, 'C[C@H](N)C')
    expect((wrapper.get('[data-testid="hplc-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    expect(requests.some((request) => request.method === 'POST' && request.url.endsWith('/predictions'))).toBe(false)
    wrapper.unmount()
  })

  it('submits HPLC 1.1 with the exact ordered raw SMILES, refs and current profile pair', async () => {
    hplcHealthStatus = 'READY'
    normalizationOriginalInputs = ['server-returned-original-one', 'server-returned-original-two']
    const rawPair = ['  C[C@H](O)C  ', 'C[C@@H](O)C'] as const
    const { wrapper } = await openPage('/prediction/hplc')
    await wrapper.get('.prediction-form select[multiple]').setValue([hplcColumns[0]])
    await wrapper.findAll('.structure-input')[0]!.get('textarea').setValue(rawPair[0])
    await wrapper.findAll('.structure-input')[1]!.get('textarea').setValue(rawPair[1])
    expect((wrapper.get('[data-testid="hplc-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    expect(normalizationCount).toBe(0)
    expect(requests.some((item) => item.method === 'POST')).toBe(false)

    await wrapper.get('[data-testid="hplc-submit"]').trigger('click')
    await flushPromises()
    expect(normalizationCount).toBe(2)
    const request = requests.find((item) => item.url.endsWith('/predictions') && item.method === 'POST')
    expect(request?.body).toBeTruthy()
    const body = JSON.parse(request!.body!)
    expect(body).toMatchObject({
      chromatography_type: 'hplc', tool: 'single', schema_version: '1.1', project_id: 'priority-local',
      structure_refs: ['structure-1', 'structure-2'],
      model_selection: {
        mode: 'exact', model_id: 'hplc-model', model_version: 'v1',
        preprocessing_profile_id: 'hplc-profile-current', preprocessing_profile_sha256: 'd'.repeat(64),
      },
      input: { column_ids: [hplcColumns[0]], flow_rate_ml_min: 1, isopropanol_fraction: 0.2, model_input_smiles: rawPair },
    })
    expect(body.input.model_input_smiles).not.toEqual(['server-returned-original-one', 'server-returned-original-two'])
    expect(request?.headers?.['idempotency-key']).toBeTruthy()
    expect(requests.filter((item) => item.method === 'POST').map((item) => item.url)).toEqual(['/api/v1/structures/normalize', '/api/v1/structures/normalize', '/api/v1/predictions'])
    wrapper.unmount()
  })

  it('blocks HPLC creation when the current model lacks its exact preprocessing pair', async () => {
    hplcHealthStatus = 'READY'
    hplcHasProfile = false
    const { wrapper } = await openPage('/prediction/hplc')
    await wrapper.get('.prediction-form select[multiple]').setValue([hplcColumns[0]])
    await normalizeStructureSlot(wrapper, 0, 'C[C@H](O)C')
    await normalizeStructureSlot(wrapper, 1, 'C[C@@H](O)C')
    expect(wrapper.text()).toContain('当前 HPLC 模型没有完整的精确预处理版本')
    expect((wrapper.get('[data-testid="hplc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('blocks HPLC 1.1 when the actual selected model only declares an older input schema', async () => {
    hplcHealthStatus = 'READY'
    hplcModelInputVersion = '1.0'
    const { wrapper } = await openPage('/prediction/hplc')
    await wrapper.get('.prediction-form select[multiple]').setValue([hplcColumns[0]])
    await normalizeStructureSlot(wrapper, 0, 'C[C@H](O)C')
    await normalizeStructureSlot(wrapper, 1, 'C[C@@H](O)C')
    expect(wrapper.text()).toContain('当前 HPLC 模型没有声明 1.1 输入能力')
    expect((wrapper.get('[data-testid="hplc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('ignores a late HPLC accepted response after a same-project type switch and capability reload', async () => {
    hplcHealthStatus = 'READY'
    const { wrapper, pinia } = await openPage('/prediction/hplc')
    await wrapper.get('.prediction-form select[multiple]').setValue([hplcColumns[0]])
    await normalizeStructureSlot(wrapper, 0, 'C[C@H](O)C')
    await normalizeStructureSlot(wrapper, 1, 'C[C@@H](O)C')
    deferHplcCreate = true
    await wrapper.get('[data-testid="hplc-submit"]').trigger('click')
    await flushPromises()
    expect(pendingHplcCreates).toHaveLength(1)

    await wrapper.findAll('[role="tab"]').find((tab) => tab.text() === 'CC')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('[role="tab"]').find((tab) => tab.text() === 'HPLC')!.trigger('click')
    await flushPromises()
    expect(usePredictionStore(pinia).selectedType).toBe('hplc')
    expect(usePredictionStore(pinia).accepted).toBeNull()
    expect(usePredictionStore(pinia).prediction).toBeNull()

    deferHplcCreate = false
    pendingHplcCreates.shift()!(envelope({ id: 'accepted-hplc-late', job_id: 'hplc-job-late', prediction_record_id: 'hplc-record-late', resource_ref: { type: 'prediction', id: 'accepted-hplc-late' }, status: 'QUEUED' }, 202))
    await flushPromises()
    expect(usePredictionStore(pinia).accepted).toBeNull()
    expect(usePredictionStore(pinia).prediction).toBeNull()
    expect(requests.some((item) => item.url.endsWith('/predictions/accepted-hplc-late'))).toBe(false)
    expect(wrapper.text()).not.toContain('任务已提交，正在等待服务端结果')
    wrapper.unmount()
  })

  it('keeps HPLC 1.0 replay snapshots readable and read-only without inventing original SMILES', async () => {
    hplcHealthStatus = 'READY'
    hplcReplayDraft = {
      id: 'hplc-draft', project_id: 'priority-local', source_record_id: 'hplc-record', source_prediction_id: 'hplc-prediction',
      chromatography_type: 'hplc', tool: 'single', schema_version: '1.0',
      input: { column_ids: ['ADH'], flow_rate_ml_min: 1, isopropanol_fraction: 0.2 },
      structure_refs: [{ type: 'structure', id: 'structure-1' }, { type: 'structure', id: 'structure-2' }],
      structure_snapshots: [
        { id: 'structure-1', project_id: 'priority-local', canonical_smiles: 'canonical-one', stereochemistry_status: 'specified' },
        { id: 'structure-2', project_id: 'priority-local', canonical_smiles: 'canonical-two', stereochemistry_status: 'specified' },
      ],
      model_selection: { mode: 'exact', model_id: 'hplc-model', model_version: 'v1' },
      source_model_snapshot: { id: 'hplc-model', version: 'v1', business_scope_id: 'scope-hplc', artifact_sha256: 'a'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0' },
      revision: 1, source_record_revision: 1, etag: '"1"', created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z',
    }
    const { wrapper } = await openPage('/prediction/hplc?draft_id=hplc-draft')
    expect(wrapper.get('[data-testid="hplc-replay-readonly"]').text()).toContain('1.0 历史草稿没有提交时的原始 SMILES')
    expect(wrapper.get('[data-testid="hplc-replay-input"]').text()).toContain('ADH')
    expect(wrapper.get('[data-testid="replay-structure-refs"]').text()).toContain('canonical-one')
    expect(wrapper.get('[data-testid="hplc-replay-input"]').text()).toContain('规范化结构不能替代原始输入')
    expect(wrapper.find('[data-testid="hplc-submit"]').exists()).toBe(false)
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('shows saved HPLC 1.1 raw snapshot and safe applicability warnings from a successful result', async () => {
    const { wrapper, pinia } = await openPage('/prediction/hplc')
    usePredictionStore(pinia).prediction = {
      id: 'hplc-result', project_id: 'priority-local', chromatography_type: 'hplc', tool: 'single', status: 'SUCCEEDED',
      model: { id: 'hplc-model', version: 'v1', output_schema_version: 'hplc-geognn-1' }, data_origin: 'production', updated_at: '2026-10-07T00:00:00Z',
      input_snapshot: { input: { column_ids: ['ADH'], flow_rate_ml_min: 1, isopropanol_fraction: 0.2, model_input_smiles: ['exact-raw-one', 'exact-raw-two'] }, normalized_structures: ['canonical-one', 'canonical-two'], structure_refs: ['structure-1', 'structure-2'] },
      result: { schema_version: 'hplc-geognn-1', task_type: 'hplc', rows: [{ candidate_id: 'ADH', separation: 1 }], metrics: [{ key: 'valid-hplc-metric', label: 'HPLC metric', value: 9.4, unit: 'min', origin: 'model' }], applicability: { supported_scope: '作者 v1.1 HPLC GeoGNN 双对映体模型', warnings: [{ code: 'HPLC_INPUT_REPRESENTATION_SENSITIVE', message: '作者模型的分子构象输入对 SMILES 表示可能敏感；本次实际输入已按原字符串保留。' }] } },
    } as unknown as ContractPrediction
    await nextTick()
    expect(wrapper.get('[data-testid="hplc-result-input"]').text()).toContain('exact-raw-one')
    expect(wrapper.get('[data-testid="hplc-result-input"]').text()).toContain('exact-raw-two')
    expect(wrapper.get('[data-testid="hplc-result-input"]').text()).toContain('本次原始 SMILES 1')
    expect(wrapper.get('[data-testid="hplc-applicability-warnings"]').text()).toContain('HPLC_INPUT_REPRESENTATION_SENSITIVE')
    expect(wrapper.get('[data-testid="hplc-applicability-warnings"]').text()).toContain('作者模型的分子构象输入对 SMILES 表示可能敏感；本次实际输入已按原字符串保留。')
    expect(wrapper.get('.result-panel .table-wrap').text()).toContain('ADH')

    usePredictionStore(pinia).prediction = {
      id: 'hplc-old-result', project_id: 'priority-local', chromatography_type: 'hplc', tool: 'single', status: 'SUCCEEDED',
      model: { id: 'hplc-model', version: 'v1', output_schema_version: 'hplc-geognn-1' }, data_origin: 'production', updated_at: '2026-10-07T00:00:00Z',
      input_snapshot: { input: { column_ids: ['ADH'], flow_rate_ml_min: 1, isopropanol_fraction: 0.2 }, normalized_structures: ['historical-canonical-one', 'historical-canonical-two'], structure_refs: ['structure-1', 'structure-2'] },
      result: { schema_version: 'hplc-geognn-1', task_type: 'hplc', rows: [], applicability: { supported_scope: 'limited' } },
    } as unknown as ContractPrediction
    await nextTick()
    expect(wrapper.get('[data-testid="hplc-result-input"]').text()).toContain('历史 1.0 记录没有保存本次原始 SMILES')
    expect(wrapper.get('[data-testid="hplc-result-input"]').text()).toContain('historical-canonical-one')
    expect(wrapper.get('[data-testid="hplc-result-input"]').text()).not.toContain('本次原始 SMILES 1')
    wrapper.unmount()
  })

  it('hides HPLC result rows and warnings when output schema is missing, unknown, or mismatched', async () => {
    const { wrapper, pinia } = await openPage('/prediction/hplc')
    const store = usePredictionStore(pinia)
    const invalidOutputs = [
      { modelOutput: undefined, resultSchema: 'hplc-geognn-1' },
      { modelOutput: 'unknown-hplc-output', resultSchema: 'hplc-geognn-1' },
      { modelOutput: 'hplc-geognn-1', resultSchema: '1.1' },
    ]
    for (const [index, identity] of invalidOutputs.entries()) {
      store.prediction = {
        id: `invalid-hplc-output-${index}`, project_id: 'priority-local', chromatography_type: 'hplc', tool: 'single', status: 'SUCCEEDED',
        model: { id: 'hplc-model', version: 'v1', ...(identity.modelOutput ? { output_schema_version: identity.modelOutput } : {}) },
        data_origin: 'production', updated_at: '2026-10-07T00:00:00Z',
        input_snapshot: { input: { column_ids: ['ADH'], flow_rate_ml_min: 1, isopropanol_fraction: 0.2, model_input_smiles: ['raw-1', 'raw-2'] }, normalized_structures: ['canonical-1', 'canonical-2'], structure_refs: ['structure-1', 'structure-2'] },
        result: { schema_version: identity.resultSchema, task_type: 'hplc', rows: [{ candidate_id: 'ADH', separation: 1 }], metrics: [{ key: 'hidden-metric', label: 'must-not-render-metric', value: 9.4, unit: 'min', origin: 'model' }], applicability: { supported_scope: 'limited', warnings: [{ code: 'MUST_NOT_RENDER_WARNING', message: 'must-not-render-warning' }] } },
        warnings: [{ code: 'MUST_NOT_RENDER_TOPLEVEL_WARNING', message: 'must-not-render-top-level-warning' }],
      } as unknown as ContractPrediction
      await nextTick()
      expect(wrapper.find('.result-panel .error-text').text()).toMatch(/HPLC (模型缺少输出 schema 身份|结果 schema 与模型声明的输出 schema 不匹配)/)
      expect(wrapper.find('.result-panel .table-wrap').exists()).toBe(false)
      expect(wrapper.find('[data-testid="hplc-applicability-warnings"]').exists()).toBe(false)
      expect(wrapper.text()).not.toContain('must-not-render-warning')
      expect(wrapper.text()).not.toContain('must-not-render-top-level-warning')
      expect(wrapper.text()).not.toContain('must-not-render')
    }
    wrapper.unmount()
  })

  it('validates GC output identity against the historical prediction model before showing values or warnings', async () => {
    const { wrapper, pinia } = await openPage('/prediction/cc')
    const store = usePredictionStore(pinia)
    const validResult = {
      task_type: 'gc', schema_version: 'gc-retention-time-1',
      rows: [{ retention_time_min: 12.5, quantiles: { q10: 10.1, q50: 12.5, q90: 14.8 } }],
      applicability: { supported_scope: 'GC selected-model scope', warnings: [] },
    }
    const modelSnapshot = {
      id: 'gc-model', version: 'gc-model-v1', business_scope_id: 'gc-default', artifact_sha256: 'a'.repeat(64),
      input_schema_version: '1.0', output_schema_version: 'gc-retention-time-1',
      preprocessing_profile_id: null, preprocessing_profile_sha256: null,
    }
    const setPrediction = (model: Record<string, unknown>, resultSchemaVersion = 'gc-retention-time-1') => {
      store.prediction = {
        id: 'gc-result-1', project_id: 'priority-local', chromatography_type: 'gc', tool: 'single', schema_version: '1.0', status: 'SUCCEEDED',
        model,
        data_origin: 'production', updated_at: '2026-10-07T00:00:00Z',
        result: { ...validResult, schema_version: resultSchemaVersion },
        warnings: [{ code: 'GC_WARNING', message: 'GC warning must be hidden when output identity is invalid' }],
      } as unknown as ContractPrediction
    }

    const missingId: Record<string, unknown> = { ...modelSnapshot }; delete missingId.id
    const missingOutput: Record<string, unknown> = { ...modelSnapshot }; delete missingOutput.output_schema_version
    for (const identity of [
      { model: missingId, resultOutput: 'gc-retention-time-1', message: 'GC 模型快照缺少必填身份字段' },
      { model: { ...modelSnapshot, artifact_sha256: 'A'.repeat(64) }, resultOutput: 'gc-retention-time-1', message: 'GC 模型快照缺少必填身份字段' },
      { model: missingOutput, resultOutput: 'gc-retention-time-1', message: 'GC 模型快照缺少必填身份字段' },
      { model: { ...modelSnapshot, output_schema_version: 'gc-output-v2' }, resultOutput: 'gc-retention-time-1', message: 'GC 结果 schema 与模型声明的输出 schema 不匹配' },
      { model: modelSnapshot, resultOutput: '1.1', message: 'GC 结果 schema 与模型声明的输出 schema 不匹配' },
    ]) {
      setPrediction(identity.model, identity.resultOutput)
      await nextTick()
      const resultPanel = wrapper.get('.result-panel')
      expect(resultPanel.text()).toContain(identity.message)
      expect(resultPanel.find('table').exists()).toBe(false)
      expect(resultPanel.text()).not.toContain('GC warning must be hidden')
      expect(resultPanel.text()).not.toContain('12.5')
    }

    setPrediction(modelSnapshot)
    await nextTick()
    const resultPanel = wrapper.get('.result-panel')
    expect(resultPanel.get('.table-wrap').text()).toContain('保留时间')
    expect(resultPanel.get('.table-wrap').text()).toContain('Q10')
    expect(resultPanel.get('.table-wrap').text()).toContain('Q50')
    expect(resultPanel.get('.table-wrap').text()).toContain('Q90')
    expect(resultPanel.get('.table-wrap').text()).toContain('10.10min')
    expect(resultPanel.get('.table-wrap').text()).toContain('12.50min')
    expect(resultPanel.get('.table-wrap').text()).toContain('14.80min')
    expect(resultPanel.get('.table-wrap').findAll('tbody tr').map((row) => row.text())).toEqual([
      '预测保留时间12.50min', 'Q1010.10min', 'Q5012.50min', 'Q9014.80min',
    ])
    expect(resultPanel.text()).toContain('GC warning must be hidden when output identity is invalid')
    expect(requests.some((request) => request.method !== 'GET')).toBe(false)
    wrapper.unmount()
  })

  it('rejects HPLC raw pairs containing only whitespace without rewriting valid input', () => {
    expect(isHplc11RawPair(['   ', 'C'])).toBe(false)
    expect(isHplc11RawPair([' C ', 'N'])).toBe(true)
  })

  it('renders nested CC candidates and schema/constraint numeric limits without submitting', async () => {
    ccHealthStatus = 'READY'
    const { wrapper } = await openPage('/prediction/cc')
    for (const [index, slot] of wrapper.findAll('.structure-input').entries()) {
      await slot.get('textarea').setValue(index === 0 ? 'C' : 'CC')
      await slot.get('.primary').trigger('click')
      await flushPromises()
    }
    expect(wrapper.get('[data-testid="cc-column-spec"]').findAll('option').map((option) => option.element.value)).toEqual(['', 'silica-4g'])
    expect(wrapper.get('[data-testid="cc-eluent-system"]').findAll('option').map((option) => option.element.value)).toEqual(['', 'cc4g-pe-ea-000-100-v1'])
    expect(wrapper.get('[data-testid="cc-loading-solvent"]').findAll('option').map((option) => option.element.value)).toEqual(['', 'cc4g-loading-dcm-v1'])
    expect(wrapper.get('[data-testid="cc-sample-mass"]').attributes()).toMatchObject({ min: '30', max: '327.3' })
    expect(wrapper.get('[data-testid="cc-loading-volume"]').attributes()).toMatchObject({ min: '200', max: '2000' })
    expect(wrapper.get('[data-testid="cc-flow-rate"]').attributes()).toMatchObject({ min: '5', max: '10' })
    expect(wrapper.get('[data-testid="cc-sample-mass"]').element).toHaveProperty('value', '30')
    expect(wrapper.get('[data-testid="cc-submit"]').element).toHaveProperty('disabled', false)
    expect(requests.some((request) => request.method !== 'GET' && !request.url.endsWith('/structures/normalize'))).toBe(false)
    wrapper.unmount()
  })

  it('submits the exact CC model selection with the profile pair returned by the current capability', async () => {
    ccHealthStatus = 'READY'
    ccProfileSha = 'c'.repeat(64)
    const { wrapper } = await openPage('/prediction/cc')
    for (const [index, slot] of wrapper.findAll('.structure-input').entries()) {
      await slot.get('textarea').setValue(index === 0 ? 'C' : 'CC')
      await slot.get('.primary').trigger('click')
      await flushPromises()
    }
    await wrapper.get('[data-testid="cc-submit"]').trigger('click')
    await flushPromises()
    const request = requests.find((item) => item.url.endsWith('/predictions') && item.method === 'POST')
    expect(request?.body && JSON.parse(request.body).model_selection).toEqual({
      mode: 'exact', model_id: 'cc-model', model_version: 'cc-qgeognn-1',
      preprocessing_profile_id: 'cc-profile-1', preprocessing_profile_sha256: 'c'.repeat(64),
    })
    expect(requests.some((item) => item.method === 'POST' && !item.url.endsWith('/structures/normalize') && !item.url.endsWith('/predictions'))).toBe(false)
    wrapper.unmount()
  })

  it('blocks a ready CC model without a profile pair before any prediction request', async () => {
    ccHealthStatus = 'READY'
    ccHasProfile = false
    const { wrapper } = await openPage('/prediction/cc')
    expect(wrapper.get('[data-testid="model-selection-error"]').text()).toContain('预处理版本')
    expect((wrapper.get('[data-testid="cc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.get('[data-testid="cc-submit"]').text()).toBe('开始 CC 预测')
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('keeps a changed profile invalidated across repeated same-page capability refreshes until the user selects again', async () => {
    ccHealthStatus = 'READY'
    const { wrapper, router } = await openPage('/prediction/cc')
    ccProfileSha = 'd'.repeat(64)
    await router.replace({ path: '/prediction/cc', query: { draft_id: '' } })
    await flushPromises()
    const select = wrapper.get('.model-strip select')
    expect((select.element as HTMLSelectElement).value).toBe('')
    expect(wrapper.text()).toContain('模型预处理版本已变化')

    await router.replace({ path: '/prediction/cc', query: {} })
    await flushPromises()
    expect((select.element as HTMLSelectElement).value).toBe('')
    expect(wrapper.text()).toContain('请显式重新选择模型')
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)

    const currentOption = select.findAll('option').find((option) => option.text().includes('cc-qgeognn-1'))!
    await select.setValue(currentOption.element.value)
    expect((select.element as HTMLSelectElement).value).not.toBe('')
    expect(wrapper.find('[data-testid="model-selection-error"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('ignores a stale capability response when same-project refreshes resolve out of order', async () => {
    ccHealthStatus = 'READY'
    const { wrapper, router } = await openPage('/prediction/cc')
    deferCcCapability = true
    await router.replace({ path: '/prediction/cc', query: { draft_id: '' } })
    await nextTick(); await flushPromises()
    await router.replace({ path: '/prediction/cc', query: {} })
    await nextTick(); await flushPromises()
    expect(pendingCcCapabilities).toHaveLength(2)

    const capabilityResponse = (sha: string) => envelope({
      id: 'cc', display_name: 'CC',
      tools: [{ id: 'single', schema_version: '1.0', health: { status: 'READY', reason: null, checked_at: '' } }],
      models: [{ id: 'cc-model', version: 'cc-qgeognn-1', task_type: 'cc', scope: 'single', preprocessing_profile_id: 'cc-profile-1', preprocessing_profile_sha256: sha, health: { status: 'READY', reason: null, checked_at: '' } }],
      limits: {}, health: { status: 'READY', reason: null, checked_at: '' },
    })
    pendingCcCapabilities[1]!(capabilityResponse('e'.repeat(64)))
    await flushPromises()
    pendingCcCapabilities[0]!(capabilityResponse('f'.repeat(64)))
    await flushPromises()
    const modelSelect = wrapper.get('.model-strip select')
    const optionValues = modelSelect.findAll('option').map((option) => option.element.value)
    expect(optionValues).toContain(JSON.stringify(['cc-model', 'cc-qgeognn-1', 'cc-profile-1', 'e'.repeat(64)]))
    expect(optionValues).not.toContain(JSON.stringify(['cc-model', 'cc-qgeognn-1', 'cc-profile-1', 'f'.repeat(64)]))
    expect((modelSelect.element as HTMLSelectElement).value).toBe('')
    expect(wrapper.text()).toContain('模型预处理版本已变化')
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('clears the old model binding on access-context changes before loading the new capability', async () => {
    ccHealthStatus = 'READY'
    const { wrapper, pinia } = await openPage('/prediction/cc')
    const appStore = useAppStore(pinia)
    const context = appStore.accessContext!
    deferCcCapability = true
    appStore.accessContext = { ...context, actor: { ...context.actor!, policy_version: 'policy-2' } } as never
    await nextTick(); await flushPromises()
    expect(pendingCcCapabilities).toHaveLength(1)
    const select = wrapper.get('.model-strip select')
    expect((select.element as HTMLSelectElement).value).toBe('')

    pendingCcCapabilities[0]!(envelope({
      id: 'cc', display_name: 'CC',
      tools: [{ id: 'single', schema_version: '1.0', health: { status: 'READY', reason: null, checked_at: '' } }],
      models: [{ id: 'cc-model', version: 'cc-qgeognn-1', task_type: 'cc', scope: 'single', preprocessing_profile_id: 'cc-profile-1', preprocessing_profile_sha256: ccProfileSha, health: { status: 'READY', reason: null, checked_at: '' } }],
      limits: {}, health: { status: 'READY', reason: null, checked_at: '' },
    }))
    await flushPromises()
    expect((select.element as HTMLSelectElement).value).toBe(JSON.stringify(['cc-model', 'cc-qgeognn-1', 'cc-profile-1', ccProfileSha]))
    expect(requests.some((item) => item.url.endsWith('/predictions') && item.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('keeps raw-ready submission available after editing and does not post until submit', async () => {
    ccHealthStatus = 'READY'
    const { wrapper, pinia } = await openPage('/prediction/cc')
    const slots = wrapper.findAll('.structure-input')
    for (const [index, slot] of slots.entries()) {
      await slot.get('textarea').setValue(index === 0 ? 'C' : 'CC')
      await slot.get('.primary').trigger('click')
      await flushPromises()
    }
    const submit = wrapper.get('[data-testid="cc-submit"]')
    expect(submit.element).toHaveProperty('disabled', false)

    const existingPrediction = { id: 'previous-prediction', project_id: 'priority-local', status: 'SUCCEEDED' } as ContractPrediction
    usePredictionStore(pinia).prediction = existingPrediction
    await slots[0]!.get('textarea').setValue('CCC')
    expect(submit.element).toHaveProperty('disabled', false)
    expect(usePredictionStore(pinia).prediction?.id).toBe(existingPrediction.id)
    expect(requests.filter((request) => request.url.endsWith('/structures/normalize'))).toHaveLength(2)
    expect(requests.some((request) => request.url.endsWith('/predictions'))).toBe(false)
    wrapper.unmount()
  })

  it('restores an out-of-range CC replay unchanged and explicitly blocks its submission', async () => {
    ccHealthStatus = 'READY'
    ccReplayDraft = {
      id: 'cc-draft', project_id: 'priority-local', source_record_id: 'cc-record', source_prediction_id: 'cc-prediction',
      chromatography_type: 'cc', tool: 'single', schema_version: '1.0',
      input: {
        column_spec_id: 'silica-4g', eluent_system_id: 'cc4g-pe-ea-000-100-v1', loading_solvent_id: 'cc4g-loading-dcm-v1',
        sample_mass_mg: 10, loading_solvent_volume_ul: 100, flow_rate_ml_min: 10,
      },
      structure_refs: [{ type: 'structure', id: 'structure-1' }, { type: 'structure', id: 'structure-2' }],
      structure_snapshots: [
        { id: 'structure-1', project_id: 'priority-local', canonical_smiles: 'C', stereochemistry_status: 'not_applicable' },
        { id: 'structure-2', project_id: 'priority-local', canonical_smiles: 'CC', stereochemistry_status: 'not_applicable' },
      ],
      model_selection: { mode: 'exact', model_id: 'cc-model', model_version: 'cc-qgeognn-1' },
      source_model_snapshot: { id: 'cc-model', version: 'cc-qgeognn-1', business_scope_id: 'scope-cc', artifact_sha256: 'a'.repeat(64), input_schema_version: '1.0', output_schema_version: 'cc-qgeognn-1' },
      revision: 1, source_record_revision: 1, etag: '"1"', created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z',
    }
    const { wrapper } = await openPage('/prediction/cc?draft_id=cc-draft')
    expect(wrapper.text()).toContain('此 CC 历史草稿没有预处理版本，只能查看原输入和结果')
    expect(wrapper.text()).toContain('sample_mass_mg')
    expect(wrapper.get('[data-testid="cc-sample-mass"]').element).toHaveProperty('value', '10')
    expect(wrapper.get('[data-testid="cc-loading-volume"]').element).toHaveProperty('value', '100')
    expect(wrapper.get('[data-testid="cc-input-validation-error"]').text()).toContain('sample_mass_mg')
    expect(wrapper.get('[data-testid="cc-submit"]').element).toHaveProperty('disabled', true)
    expect(requests.some((request) => request.method !== 'GET')).toBe(false)
    wrapper.unmount()
  })

  it('shows CC metric DTO labels, values, units and derived origins only for a validated successful result', async () => {
    ccHealthStatus = 'READY'
    const { wrapper, pinia } = await openPage('/prediction/cc')
    const metrics = [
      { key: 'structure_1_V1_volume_q10', label: 'Structure 1 V1 volume q10', value: 31.2, unit: 'ml', origin: 'model' },
      { key: 'structure_1_V1_time_q10', label: 'Structure 1 V1 time q10', value: 187.2, unit: 's', origin: 'derived' },
      { key: 'structure_2_V2_volume_q90', label: 'Structure 2 V2 volume q90', value: 88.6, unit: 'ml', origin: 'model' },
      { key: 'structure_2_V2_time_q90', label: 'Structure 2 V2 time q90', value: 531.6, unit: 's', origin: 'derived' },
    ]
    usePredictionStore(pinia).prediction = {
      id: 'prediction-1', project_id: 'priority-local', chromatography_type: 'cc', tool: 'single', status: 'SUCCEEDED',
      model: { id: 'cc-model', version: 'cc-qgeognn-1' }, data_origin: 'model', updated_at: '2026-10-07T00:00:00Z',
      warnings: [],
      result: { task_type: 'cc', schema_version: 'cc-qgeognn-1',
        rows: [{ volume_ml: 2, flow_rate_ml_min: 3, time_seconds: 40 }], metrics,
        applicability: { supported_scope: 'CC 4g', warnings: [
          { code: 'EXPERIMENTAL_CONFIRMATION_REQUIRED', message: '结果须通过实验确认。', field: 'model' },
          { code: 'NO_INDEPENDENT_GENERALIZATION_EVIDENCE', message: '当前没有独立泛化验证证据。' },
        ] } },
    } as never
    await flushPromises()
    const metricsPanel = wrapper.get('[data-testid="cc-result-metrics"]')
    expect(metricsPanel.text()).toContain('Structure 1 V1 volume q10')
    expect(metricsPanel.text()).toContain('Structure 2 V2 time q90')
    expect(metricsPanel.text()).toContain('模型返回')
    expect(metricsPanel.text()).toContain('服务端派生')
    expect(metricsPanel.text()).toContain('88.6')
    const warningsPanel = wrapper.get('[data-testid="cc-applicability-warnings"]')
    expect(warningsPanel.text()).toContain('EXPERIMENTAL_CONFIRMATION_REQUIRED：结果须通过实验确认。')
    expect(warningsPanel.text()).toContain('NO_INDEPENDENT_GENERALIZATION_EVIDENCE：当前没有独立泛化验证证据。')
    expect(warningsPanel.text()).not.toContain('field')
    expect(wrapper.text()).toContain('体积（mL，模型返回）')
    expect(wrapper.text()).toContain('时间（s，服务端派生）')

    usePredictionStore(pinia).prediction = {
      ...usePredictionStore(pinia).prediction!, status: 'FAILED',
    }
    await flushPromises()
    expect(wrapper.find('[data-testid="cc-result-metrics"]').exists()).toBe(false)
    expect(requests.some((request) => request.method !== 'GET')).toBe(false)
    wrapper.unmount()
  })
})
