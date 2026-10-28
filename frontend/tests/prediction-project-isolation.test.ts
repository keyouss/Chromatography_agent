import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, inject, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import type { ContractPrediction } from '@/core/contract-types'
import type { Structure } from '@/core/types'
import { structureWorkspaceKey, type StructureTargetController } from '@/core/structure/workspace'
import { usePredictionStore } from '@/shared/prediction/store'
import { emptyPredictionWorkspace, predictionWorkspaceKey, writePredictionWorkspace } from '@/shared/prediction/workspace'
import { useAppStore } from '@/stores/app'
import { areDistinctStructureRefs } from '@/modules/prediction/input-validation'
import TlcReverseWorkspace from '@/shared/tlc-reverse/TlcReverseWorkspace.vue'
import PredictionView from '@/modules/prediction/PredictionView.vue'

vi.mock('@/app/generated/profile', () => ({ activeProfile: { id: 'test', modules: ['prediction', 'utilities'], chromatographyTypes: ['tlc'], predictionTools: { tlc: ['single', 'condition_compare', 'reverse_solvent'] }, utilityFeatures: ['tlc_reverse_solvent'], assistantModes: [] } }))

describe('CC structure reference validation', () => {
  it('requires exactly two non-empty, distinct normalized structure refs', () => {
    expect(areDistinctStructureRefs([])).toBe(false)
    expect(areDistinctStructureRefs(['structure-a'])).toBe(false)
    expect(areDistinctStructureRefs(['structure-a', 'structure-b'])).toBe(true)
    expect(areDistinctStructureRefs(['structure-a', 'structure-a'])).toBe(false)
    expect(areDistinctStructureRefs(['a', ' a '])).toBe(false)
    expect(areDistinctStructureRefs(['structure-a', 'structure-b', 'structure-c'])).toBe(false)
    expect(areDistinctStructureRefs(['structure-a', '  '])).toBe(false)
  })
})

function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, request_id: 'prediction-project-isolation', warnings: [] }), { status, headers: { 'content-type': 'application/json' } })
}

function prediction(id: string, projectId: string, type = 'tlc'): ContractPrediction {
  return { id, project_id: projectId, chromatography_type: type, tool: 'single', status: 'SUCCEEDED' } as unknown as ContractPrediction
}

const accessContext = (projectId: string, actions = ['*']) => ({
  actor: { id: 'actor', auth_mode: 'deployment_default', policy_version: 'policy-1' }, actor_id: 'actor', auth_mode: 'deployment_default',
  allowed_actions: actions, permissions: actions, is_multi_user_authenticated: false,
  project_selection: 'resolved', accessible_projects: [{ id: projectId, name: projectId }], default_project_id: projectId,
})

let structureNormalizeProjects: string[] = []
const structureInputStub = defineComponent({
  props: { projectId: { type: String, required: true }, targetId: { type: String, default: 'primary' } },
  emits: ['normalized', 'invalidated', 'draft-change'],
  setup(props, { emit }) {
    const workspace = inject(structureWorkspaceKey, null)
    const smiles = ref('')
    const disabled = ref(false)
    const controller: StructureTargetController = {
      id: props.targetId,
      label: '测试结构',
      projectId: props.projectId,
      disabled,
      smiles,
      getSmiles: () => smiles.value,
      setSmiles(value) { smiles.value = value; emit('invalidated'); emit('draft-change', value) },
      invalidate() { emit('invalidated') },
      async normalize(): Promise<Structure | null> {
        const value = smiles.value
        if (!value.trim()) return null
        structureNormalizeProjects.push(props.projectId)
        const structure = { id: `str-${props.projectId}`, project_id: props.projectId, canonical_smiles: value } as Structure
        emit('normalized', structure)
        return structure
      },
    }
    let unregister: (() => void) | undefined
    onMounted(() => { unregister = workspace?.registerTarget(controller) })
    onBeforeUnmount(() => unregister?.())
    return () => h('div', [
      h('textarea', { 'data-testid': `raw-${props.targetId}`, value: smiles.value, onInput: (event: Event) => controller.setSmiles((event.target as HTMLTextAreaElement).value) }),
      h('button', { 'data-testid': `normalize-${props.projectId}`, onClick: () => { if (!smiles.value.trim()) controller.setSmiles('CC'); void controller.normalize() } }, `Normalize ${props.projectId}`),
    ])
  },
})

const reversePanelStub = defineComponent({
  props: { structureRef: { type: String, default: null }, structureInputReady: Boolean, initialInput: { type: Object, default: null }, busy: Boolean }, emits: ['submit-reverse', 'submit-single', 'submit-comparison'],
  setup(props, { emit }) {
    const structureReady = () => Boolean(props.structureRef || props.structureInputReady)
    return () => h('div', [
      h('button', { 'data-testid': 'reverse-submit', disabled: !structureReady() || props.busy, onClick: () => emit('submit-reverse', { target_rf: { min: 0.2, max: 0.3 }, candidate_system_ids: ['hexane/ethyl_acetate'], ratio_step: 0.5, max_candidates: 1 }) }, 'Submit reverse'),
      h('button', { 'data-testid': 'single-submit', disabled: !structureReady() || props.busy, onClick: () => emit('submit-single', { solvent_components: [{ solvent_id: 'hexane', fraction: 0.5 }, { solvent_id: 'ethyl_acetate', fraction: 0.5 }] }) }, 'Submit single'),
      h('button', { 'data-testid': 'comparison-submit', disabled: props.busy, onClick: () => emit('submit-comparison', [{ solvent_components: [{ solvent_id: 'hexane', fraction: 0.5 }, { solvent_id: 'ethyl_acetate', fraction: 0.5 }] }]) }, 'Submit comparison'),
    ])
  },
})

function mountReverse() {
  const pinia = createPinia(); setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] })
  const app = useAppStore(pinia); app.accessContext = accessContext('project-a') as never; app.state = 'ready'
  const requests: Array<{ url: string; method: string; body?: Record<string, unknown> }> = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET'
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : undefined
    requests.push({ url, method, body })
    if (url.endsWith('/prediction/types/tlc')) return envelope({ id: 'tlc', display_name: 'TLC', tools: [{ id: 'reverse_solvent', schema_version: '1.0' }], models: [{ id: 'tlc-model', version: 'v1', task_type: 'tlc_reverse_solvent', scope: 'project', health: { status: 'READY', reason: null, checked_at: '' } }], limits: {}, health: { status: 'READY', reason: null, checked_at: '' } })
    if (url.includes('/prediction/types/tlc/tools/reverse_solvent/schema')) return envelope({ id: 'reverse_solvent', schema_version: '1.0', input_schema: {}, output_schema: {}, structure_count: { min: 1, max: 1, semantics: 'single' }, model_constraints: {}, units: {}, supported_visualizations: [], limits: {}, health: { status: 'READY', reason: null, checked_at: '' } })
    if (url.endsWith('/tlc/conditions/options')) return envelope({ solvent_options: [{ id: 'hexane', label: 'Hexane' }, { id: 'ethyl_acetate', label: 'Ethyl acetate' }], candidate_systems: [{ id: 'hexane/ethyl_acetate', solvent_ids: ['hexane', 'ethyl_acetate'] }], reverse: { target_rf: { min: 0, max: 1 }, ratio_step: { min: 0.5, max: 1, min_inclusive: true, max_inclusive: true }, max_candidates: 5 } })
    if (url.endsWith('/tlc/reverse-solvent') && method === 'POST') return envelope({ id: 'pred-project-b', job_id: 'job-project-b', status: 'QUEUED', resource_ref: { type: 'prediction', id: 'pred-project-b' } }, 202)
    if (url.endsWith('/predictions/pred-project-b')) return envelope(prediction('pred-project-b', 'project-b'))
    throw new Error(`Unexpected request ${method} ${url}`)
  }))
  const wrapper = mount(TlcReverseWorkspace, { global: { plugins: [pinia, router], stubs: { RouterLink: true, StructureInput: structureInputStub, TlcToolPanel: reversePanelStub } } })
  return { app, requests, store: usePredictionStore(pinia), wrapper }
}

async function mountPrediction(options: { replayDraft?: Record<string, unknown>; toolSchemaId?: string; toolSchemaVersion?: string; queryDraftId?: string; type?: string; modelProfile?: { id: string; sha256: string } } = {}) {
  const pinia = createPinia(); setActivePinia(pinia)
  const app = useAppStore(pinia); app.accessContext = accessContext('project-a') as never; app.state = 'ready'
  const requests: Array<{ url: string; method: string; body?: Record<string, unknown> }> = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET'
    const body = typeof init?.body === 'string' ? JSON.parse(init.body) as Record<string, unknown> : undefined
    requests.push({ url, method, body })
    if (url.endsWith('/prediction/types')) return envelope([
      ...(options.type === 'sfc' ? [{ id: 'sfc', display_name: 'SFC', tools: ['full_column'], health: { status: 'READY', reason: null, checked_at: '' } }] : []),
      { id: 'tlc', display_name: 'TLC', tools: ['single', 'condition_compare', 'reverse_solvent'], health: { status: 'READY', reason: null, checked_at: '' } },
    ])
    if (url.endsWith('/prediction/types/tlc')) return envelope({ id: 'tlc', display_name: 'TLC', tools: [{ id: 'single', schema_version: '1.0', health: { status: 'READY', reason: null, checked_at: '' } }, { id: 'condition_compare', schema_version: '1.0', health: { status: 'READY', reason: null, checked_at: '' } }], models: [{ id: 'tlc-model', version: 'v1', task_type: 'tlc', scope: 'project', ...(options.modelProfile ? { preprocessing_profile_id: options.modelProfile.id, preprocessing_profile_sha256: options.modelProfile.sha256 } : {}), health: { status: 'READY', reason: null, checked_at: '' } }], limits: {}, health: { status: 'READY', reason: null, checked_at: '' } })
    if (url.includes('/prediction/types/tlc/tools/condition_compare/schema')) return envelope({ id: 'condition_compare', schema_version: '1.0', input_schema: {}, output_schema: {}, structure_count: { min: 1, max: 1, semantics: 'single' }, model_constraints: {}, units: {}, supported_visualizations: [], limits: {}, health: { status: 'READY', reason: null, checked_at: '' } })
    if (url.includes('/prediction-drafts/')) return options.replayDraft ? envelope(options.replayDraft) : envelope({}, 404)
    if (url.includes('/prediction/types/tlc/tools/single/schema')) return envelope({ id: options.toolSchemaId ?? 'single', schema_version: options.toolSchemaVersion ?? '1.0', input_schema: {}, output_schema: {}, structure_count: { min: 1, max: 1, semantics: 'single' }, model_constraints: {}, units: {}, supported_visualizations: [], limits: {}, health: { status: 'READY', reason: null, checked_at: '' } })
    if (url.endsWith('/tlc/conditions/options')) return envelope({ solvent_options: [{ id: 'hexane', label: 'Hexane' }, { id: 'ethyl_acetate', label: 'Ethyl acetate' }], candidate_systems: [{ id: 'hexane/ethyl_acetate', solvent_ids: ['hexane', 'ethyl_acetate'] }], reverse: { target_rf: { min: 0, max: 1 }, ratio_step: { min: 0.5, max: 1, min_inclusive: true, max_inclusive: true }, max_candidates: 5 } })
    if (url.endsWith('/structures/normalize') && method === 'POST') return envelope({ id: `str-${String(body?.project_id)}`, project_id: body?.project_id, canonical_smiles: body?.value })
    if (url.endsWith('/predictions') && method === 'POST') return envelope({ id: 'pred-project-b', job_id: 'job-project-b', status: 'QUEUED', resource_ref: { type: 'prediction', id: 'pred-project-b' } }, 202)
    if (url.endsWith('/tlc/condition-comparisons') && method === 'POST') return envelope({ id: 'pred-project-b', job_id: 'job-project-b', status: 'QUEUED', resource_ref: { type: 'prediction', id: 'pred-project-b' } }, 202)
    if (url.endsWith('/predictions/pred-project-b')) return envelope(prediction('pred-project-b', 'project-b'))
    throw new Error(`Unexpected request ${method} ${url}`)
  }))
  const type = options.type ?? 'tlc'
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: `/prediction/${type}`, component: PredictionView, meta: { chromatographicType: type } }] })
  await router.push({ path: `/prediction/${type}`, query: options.queryDraftId ? { draft_id: options.queryDraftId } : {} }); await router.isReady()
  const wrapper = mount(PredictionView, { global: { plugins: [pinia, router], stubs: { StructureInput: structureInputStub, TlcToolPanel: reversePanelStub } } })
  return { app, requests, store: usePredictionStore(pinia), wrapper }
}

beforeEach(() => {
  structureNormalizeProjects = []
  setActivePinia(createPinia()); vi.restoreAllMocks()
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) } })
  for (const project of ['project-a', 'project-b']) {
    for (const tool of ['single', 'batch', 'condition_compare', 'reverse_solvent']) {
      const key = predictionWorkspaceKey('actor', project, 'tlc', tool)
      writePredictionWorkspace(key, emptyPredictionWorkspace('actor', project, 'tlc', tool))
    }
  }
})

describe('prediction project isolation', () => {
  const replayDraft = {
    id: 'draft-replay-a', source_record_id: 'record-a', source_prediction_id: 'prediction-a', project_id: 'project-a', chromatography_type: 'tlc', tool: 'single',
    structure_refs: [{ type: 'structure', id: 'str-project-a' }],
    structure_snapshots: [{ id: 'str-project-a', project_id: 'project-a', canonical_smiles: 'CC', stereochemistry_status: 'unspecified' }],
    input: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.5 }, { solvent_id: 'ethyl_acetate', fraction: 0.5 }] },
    schema_version: '1.0', model_selection: { mode: 'exact', model_id: 'tlc-model', model_version: 'v1' },
    source_model_snapshot: { id: 'tlc-model', version: 'v1', business_scope_id: 'tlc-default', artifact_sha256: 'a'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0' },
    source_record_revision: 1, revision: 1, etag: 'draft-etag-a', created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z',
  }
  const sfcReplayDraft = {
    id: 'draft-sfc-readonly', source_record_id: 'record-sfc', source_prediction_id: 'prediction-sfc', project_id: 'project-a', chromatography_type: 'sfc', tool: 'full_column',
    structure_refs: [{ type: 'structure', id: 'str-sfc-a' }, { type: 'structure', id: 'str-sfc-b' }],
    structure_snapshots: [
      { id: 'str-sfc-a', project_id: 'project-a', canonical_smiles: 'C[C@H](N)C(=O)O', stereochemistry_status: 'specified' },
      { id: 'str-sfc-b', project_id: 'project-a', canonical_smiles: 'C[C@@H](N)C(=O)O', stereochemistry_status: 'specified' },
    ],
    modifier_structure_snapshots: [{ id: 'str-modifier-a', project_id: 'project-a', canonical_smiles: 'CO', stereochemistry_status: 'not_applicable' }],
    input: { candidate_scope_id: 'scope-a', modifier_components: [{ structure_ref: 'str-modifier-a', fraction: 1 }], modifier_total_fraction: 1, top_k: 1 },
    schema_version: 'sfc-v1', model_selection: { mode: 'exact', model_id: 'sfc-model', model_version: 'v1' },
    source_model_snapshot: { id: 'sfc-model', version: 'v1', business_scope_id: 'sfc-default', artifact_sha256: 'b'.repeat(64), input_schema_version: 'sfc-v1', output_schema_version: 'sfc-v1' },
    source_record_revision: 1, revision: 1, etag: 'draft-etag-sfc', created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z',
  }
  const comparisonReplayDraft = {
    ...replayDraft, id: 'draft-tlc-comparison', tool: 'condition_compare',
  }

  it('shows SFC full-column draft as read-only and the submit handler cannot create a prediction', async () => {
    const { requests, wrapper } = await mountPrediction({ replayDraft: sfcReplayDraft, queryDraftId: sfcReplayDraft.id, type: 'sfc' })
    await flushPromises()
    expect(requests.some((request) => request.method === 'GET' && request.url.includes(`/prediction-drafts/${sfcReplayDraft.id}?project_id=project-a`))).toBe(true)
    expect(wrapper.get('[data-testid="replay-draft-status"]').text()).toContain(sfcReplayDraft.id)
    expect(wrapper.get('[data-testid="replay-draft-unsupported"]').text()).toContain('此工具暂不能安全加载为可编辑表单')
    expect(wrapper.get('[role="alert"]').text()).toContain('SFC 草稿冻结输入只读保留；不会自动创建候选范围或重新绑定模型。请创建新的 SFC 1.1 搜索。')

    const setup = (wrapper.vm.$ as unknown as { setupState: Record<string, unknown> }).setupState
    const submitSfc = setup.submitSfc as (value: unknown) => Promise<void>
    await submitSfc({ top_k: 1 })
    expect(requests.some((request) => request.method === 'POST' && request.url.endsWith('/predictions'))).toBe(false)
    expect(requests.some((request) => request.method === 'POST' && request.url.includes('/candidate-scopes'))).toBe(false)
    wrapper.unmount()
  })

  it('shows non-single TLC draft as read-only and blocks a child submit event', async () => {
    const { requests, wrapper } = await mountPrediction({ replayDraft: comparisonReplayDraft, queryDraftId: comparisonReplayDraft.id })
    await flushPromises()
    expect(requests.some((request) => request.method === 'GET' && request.url.includes(`/prediction-drafts/${comparisonReplayDraft.id}?project_id=project-a`))).toBe(true)
    expect(wrapper.get('[data-testid="replay-draft-status"]').text()).toContain(comparisonReplayDraft.id)
    expect(wrapper.get('[data-testid="replay-draft-unsupported"]').text()).toContain('此工具暂不能安全加载为可编辑表单')
    expect((wrapper.get('[data-testid="single-submit"]').element as HTMLButtonElement).disabled).toBe(true)

    wrapper.getComponent(reversePanelStub).vm.$emit('submit-single', replayDraft.input)
    await flushPromises()
    expect(requests.some((request) => request.method === 'POST' && request.url.endsWith('/predictions'))).toBe(false)
    wrapper.unmount()
  })

  it('loads the server draft through the route id and restores input with the exact model', async () => {
    const { requests, wrapper } = await mountPrediction({ replayDraft, queryDraftId: replayDraft.id })
    await flushPromises()
    expect(requests.some((request) => request.url.includes(`/prediction-drafts/${replayDraft.id}?project_id=project-a`) && request.method === 'GET')).toBe(true)
    expect(wrapper.get('[data-testid="replay-draft-status"]').text()).toContain(replayDraft.id)
    expect(wrapper.findAll('select').map((select) => (select.element as HTMLSelectElement).value)).toContain(JSON.stringify(['tlc-model', 'v1', null, null]))
    expect(wrapper.getComponent(reversePanelStub).props('initialInput')).toEqual(replayDraft.input)
    wrapper.unmount()
  })

  it('rejects an unknown top-level property on the additionalProperties-false replay draft', async () => {
    const draft = { ...replayDraft, internal_debug_path: 'C:/private/model.pth' }
    localStorage.setItem('chrom-platform.replay-draft', JSON.stringify({ 'project-a': draft.id }))
    const { requests, wrapper } = await mountPrediction({ replayDraft: draft, queryDraftId: draft.id })
    await flushPromises()

    expect(wrapper.get('[role="alert"]').text()).toContain('包含合同外字段')
    expect(wrapper.find('[data-testid="replay-draft-blocked"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('C:/private/model.pth')
    await wrapper.get('[data-testid="single-submit"]').trigger('click')
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('restores the persisted project pointer on re-entry without a route draft id', async () => {
    localStorage.setItem('chrom-platform.replay-draft', JSON.stringify({ 'project-a': replayDraft.id }))
    const { requests, wrapper } = await mountPrediction({ replayDraft })
    await flushPromises()
    expect(requests.some((request) => request.url.includes(`/prediction-drafts/${replayDraft.id}?project_id=project-a`) && request.method === 'GET')).toBe(true)
    expect(wrapper.get('[data-testid="replay-draft-status"]').text()).toContain(replayDraft.id)
    expect(wrapper.getComponent(reversePanelStub).props('initialInput')).toEqual(replayDraft.input)
    wrapper.unmount()
  })

  it.each([false, true])('starts a new prediction from replay without changing server drafts or other workspaces (route=%s)', async (explicitRoute) => {
    localStorage.setItem('chrom-platform.replay-draft', JSON.stringify({ 'project-a': replayDraft.id, 'project-b': 'other-draft' }))
    const otherWorkspace = 'chrom-platform.prediction-workspace-v1:actor:project-a:gc:single'
    localStorage.setItem(otherWorkspace, 'other type draft')
    const { wrapper, requests } = await mountPrediction({ replayDraft, queryDraftId: explicitRoute ? replayDraft.id : undefined })
    await flushPromises()
    expect(wrapper.find('[data-testid="replay-draft-status"]').exists()).toBe(true)

    await wrapper.get('[data-testid="new-prediction"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="replay-draft-status"]').exists()).toBe(false)
    expect(wrapper.vm.$router.currentRoute.value.query.draft_id).toBeUndefined()
    expect(wrapper.get('[data-testid="raw-primary"]').element).toHaveProperty('value', '')
    expect(JSON.parse(localStorage.getItem('chrom-platform.replay-draft')!)).toEqual({ 'project-b': 'other-draft' })
    expect(localStorage.getItem(otherWorkspace)).toBe('other type draft')
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it.each([
    ['schema tool id differs from the requested capability tool', { toolSchemaId: 'condition_compare', toolSchemaVersion: '1.0' }],
    ['schema version differs from the capability tool version', { toolSchemaId: 'single', toolSchemaVersion: '0.9' }],
  ])('fails closed when replay %s', async (_label, schema) => {
    const draft = {
      id: 'draft-schema-mismatch', source_record_id: 'record-a', source_prediction_id: 'prediction-a', project_id: 'project-a', chromatography_type: 'tlc', tool: 'single',
      structure_refs: [{ type: 'structure', id: 'str-project-a' }],
      structure_snapshots: [{ id: 'str-project-a', project_id: 'project-a', canonical_smiles: 'CC', stereochemistry_status: 'unspecified' }],
      input: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.5 }, { solvent_id: 'ethyl_acetate', fraction: 0.5 }] },
      schema_version: '1.0', model_selection: { mode: 'exact', model_id: 'tlc-model', model_version: 'v1' },
      source_model_snapshot: { id: 'tlc-model', version: 'v1', business_scope_id: 'tlc-default', artifact_sha256: 'a'.repeat(64), input_schema_version: '1.0', output_schema_version: '1.0' },
      source_record_revision: 1, revision: 1, etag: 'draft-etag-schema', created_at: '2026-10-04T00:00:00Z', updated_at: '2026-10-04T00:00:00Z',
    }
    localStorage.setItem('chrom-platform.replay-draft', JSON.stringify({ 'project-a': draft.id }))
    const { requests, wrapper } = await mountPrediction({ replayDraft: draft, ...schema })
    await flushPromises()

    expect(wrapper.find('[data-testid="replay-draft-status"]').exists()).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toContain('草稿工具或 schema 版本与当前服务端能力不匹配')
    expect((wrapper.get('[data-testid="single-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('[data-testid="single-submit"]').trigger('click')
    expect(requests.some((request) => request.url.endsWith('/predictions') && request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('does not let a late project A GET overwrite the current project B result', async () => {
    let resolveA!: (response: Response) => void
    const getA = new Promise<Response>((resolve) => { resolveA = resolve })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => String(input).endsWith('/pred-a') ? getA : envelope(prediction('pred-b', 'project-b')))
    const store = usePredictionStore()
    store.prediction = prediction('pred-b', 'project-b')
    let currentProject = 'project-a'
    const pending = store.getPrediction('pred-a', () => currentProject === 'project-a', 'project-a', 'tlc')

    currentProject = 'project-b'
    resolveA(envelope(prediction('pred-a', 'project-a')))
    await expect(pending).resolves.toMatchObject({ id: 'pred-a', project_id: 'project-a' })
    expect(store.prediction).toMatchObject({ id: 'pred-b', project_id: 'project-b' })
  })

  it('ignores a legacy type-only pointer and does not fetch without a workspace reference', async () => {
    localStorage.setItem('chrom-platform.prediction', JSON.stringify({ predictionIdsByProject: { 'project-a': { tlc: 'legacy-a' } } }))
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    const store = usePredictionStore()
    expect(store.prediction).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('keeps an unknown submission locked when the user switches TLC tools while POST is pending', async () => {
    const { wrapper } = await mountPrediction()
    await flushPromises()
    const fallbackFetch = globalThis.fetch
    let rejectPost!: (reason: Error) => void
    let postStarted = false
    let postCount = 0
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/predictions') && init?.method === 'POST') {
        postStarted = true
        postCount += 1
        return new Promise<Response>((_resolve, reject) => { rejectPost = reject })
      }
      return fallbackFetch(input, init)
    }))

    await wrapper.get('[data-testid="normalize-project-a"]').trigger('click')
    await wrapper.get('[data-testid="single-submit"]').trigger('click')
    await flushPromises()
    expect(postStarted).toBe(true)

    const toolButtons = wrapper.findAll('[aria-label="TLC 工具"] button')
    await toolButtons.find((button) => button.text() === '条件比较')!.trigger('click')
    await flushPromises()
    await wrapper.findAll('[aria-label="TLC 工具"] button').find((button) => button.text() === '单次预测')!.trigger('click')
    await flushPromises()
    rejectPost(new Error('connection lost after request'))
    await flushPromises()

    expect(wrapper.find('[data-testid="submission-status-unknown"]').exists()).toBe(true)
    expect(postCount).toBe(1)
    wrapper.unmount()
  })

  it('clears project A state on project switch and requires a project B structure before a request', async () => {
    const { app, requests, store, wrapper } = mountReverse()
    await flushPromises()
    await wrapper.get('[data-testid="normalize-project-a"]').trigger('click')
    store.prediction = prediction('pred-project-a', 'project-a')
    store.accepted = { id: 'pred-project-a', job_id: 'job-project-a', status: 'QUEUED', resource_ref: { type: 'prediction', id: 'pred-project-a' } } as never

    app.accessContext = accessContext('project-b') as never
    await nextTick(); await flushPromises()
    expect(store.prediction).toBeNull()
    expect(store.accepted).toBeNull()
    expect(wrapper.find('[data-testid="normalize-project-a"]').exists()).toBe(false)
    expect((wrapper.get('[data-testid="reverse-submit"]').element as HTMLButtonElement).disabled).toBe(true)

    await wrapper.get('[data-testid="reverse-submit"]').trigger('click')
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
    await wrapper.get('[data-testid="normalize-project-b"]').trigger('click')
    await wrapper.get('[data-testid="reverse-submit"]').trigger('click')
    await flushPromises()
    const request = requests.find((item) => item.url.endsWith('/tlc/reverse-solvent') && item.method === 'POST')
    expect(request?.body).toMatchObject({ project_id: 'project-b', structure_ref: 'str-project-b' })
    wrapper.unmount()
  })

  it('clears same-project state and blocks submit when access actions change', async () => {
    const { app, requests, store, wrapper } = mountReverse()
    await flushPromises()
    await wrapper.get('[data-testid="normalize-project-a"]').trigger('click')
    store.prediction = prediction('pred-project-a', 'project-a')

    app.accessContext = accessContext('project-a', ['prediction:read']) as never
    await nextTick(); await flushPromises()
    expect(store.prediction).toBeNull()
    expect(wrapper.findAll('[data-testid="prediction-permission-blocked"]')).toHaveLength(1)
    expect(wrapper.findAll('[data-testid="normalize-project-a"]')).toHaveLength(0)
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
    wrapper.unmount()
  })

  it('keeps PredictionView project-scoped and normalizes a new raw structure only after submit', async () => {
    const { app, requests, store, wrapper } = await mountPrediction()
    await flushPromises()
    await wrapper.get('[data-testid="normalize-project-a"]').trigger('click')
    store.prediction = prediction('pred-project-a', 'project-a')
    store.accepted = { id: 'pred-project-a', job_id: 'job-project-a', status: 'QUEUED', resource_ref: { type: 'prediction', id: 'pred-project-a' } } as never

    app.accessContext = accessContext('project-b') as never
    await nextTick(); await flushPromises()
    expect(store.prediction).toBeNull()
    expect(store.accepted).toBeNull()
    expect(wrapper.findAll('[data-testid="normalize-project-a"]')).toHaveLength(0)
    expect((wrapper.get('[data-testid="single-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('[data-testid="single-submit"]').trigger('click')
    expect(requests.some((request) => request.method === 'POST')).toBe(false)

    await wrapper.get('[data-testid="raw-primary"]').setValue('CCN')
    expect((wrapper.get('[data-testid="single-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
    await wrapper.get('[data-testid="single-submit"]').trigger('click')
    await flushPromises()
    expect(structureNormalizeProjects.filter((projectId) => projectId === 'project-b')).toHaveLength(1)
    const request = requests.find((item) => item.url.endsWith('/predictions') && item.method === 'POST')
    expect(request?.body).toMatchObject({ project_id: 'project-b', structure_refs: ['str-project-b'] })
    wrapper.unmount()
  })

  it('passes a declared non-CC profile pair through the TLC comparison request', async () => {
    const profile = { id: 'tlc-preprocess-v1', sha256: 'c'.repeat(64) }
    const { requests, wrapper } = await mountPrediction({ modelProfile: profile })
    await flushPromises()
    await wrapper.findAll('[aria-label="TLC 工具"] button').find((button) => button.text() === '条件比较')!.trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="normalize-project-a"]').trigger('click')
    await wrapper.get('[data-testid="comparison-submit"]').trigger('click')
    await flushPromises()
    const request = requests.find((item) => item.url.endsWith('/tlc/condition-comparisons') && item.method === 'POST')
    expect(request?.body?.model_selection).toEqual({
      mode: 'exact', model_id: 'tlc-model', model_version: 'v1',
      preprocessing_profile_id: profile.id, preprocessing_profile_sha256: profile.sha256,
    })
    wrapper.unmount()
  })
})
