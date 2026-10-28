import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h, inject, onBeforeUnmount, onMounted, nextTick, ref } from 'vue'
import { predictionModule } from '../src/modules/prediction/module'
import { predictionTlcExtension } from '../src/modules/prediction/tlc-entry'
import { predictionTlcReverseExtension } from '../src/modules/prediction/tlc-reverse-entry'
import { utilitiesTlcReverseExtension } from '../src/modules/utilities/tlc-reverse-entry'
import { mapTlcReverseCurve, type TlcReverseResult } from '../src/shared/prediction/result'
import TlcToolPanel from '../src/modules/prediction/forms/tlc/TlcToolPanel.vue'
import TlcReverseWorkspace from '../src/shared/tlc-reverse/TlcReverseWorkspace.vue'
import { usePredictionStore } from '../src/shared/prediction/store'
import { emptyPredictionWorkspace, predictionWorkspaceKey, writePredictionWorkspace } from '../src/shared/prediction/workspace'
import { useAppStore } from '../src/stores/app'
import type { ContractPrediction, ContractTlcReverseBody } from '../src/core/contract-types'
import { structureWorkspaceKey, type StructureTargetController } from '../src/core/structure/workspace'
import type { Structure } from '../src/core/types'

beforeEach(() => {
  setActivePinia(createPinia()); vi.restoreAllMocks()
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) } })
  const key = predictionWorkspaceKey('actor', 'project-a', 'tlc', 'reverse_solvent')
  writePredictionWorkspace(key, emptyPredictionWorkspace('actor', 'project-a', 'tlc', 'reverse_solvent'))
})

const baseResult: TlcReverseResult = {
  task_type: 'tlc_reverse_solvent', schema_version: '1.0', structure_ref: 'str-real', model_id: 'model-real', model_version: 'v1', artifact_sha256: 'a'.repeat(64),
  candidates: [], evaluated: 0, failed: 0, target_reachable: false, warnings: ['TARGET_UNREACHABLE: no directly predicted solvent point reaches target Rf'],
}

function mockCanvasContexts() {
  const contexts = new WeakMap<HTMLCanvasElement, { strokes: Array<{ style: string; path: number[][] }> }>()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    let path: number[][] = []
    const record = { strokes: [] as Array<{ style: string; path: number[][] }> }
    const context = {
      strokeStyle: '', fillStyle: '', lineWidth: 1, font: '', textAlign: '', textBaseline: '',
      setTransform() {}, clearRect() {}, beginPath() { path = [] },
      moveTo(x: number, y: number) { path.push([x, y]) },
      lineTo(x: number, y: number) { path.push([x, y]) },
      stroke() { record.strokes.push({ style: context.strokeStyle, path: [...path] }) },
      fillText() {}, setLineDash() {}, arc() {}, fill() {}, fillRect() {},
      createRadialGradient() { return { addColorStop() {} } },
    }
    contexts.set(this, record)
    return context as unknown as CanvasRenderingContext2D
  })
  return contexts
}

function renderReverse(result: TlcReverseResult) {
  const mapped = mapTlcReverseCurve(result, { target_rf_min: 0.4, target_rf_max: 0.6, ratio_step: 0.25 }, 'SUCCEEDED')
  const wrapper = mount(TlcToolPanel, {
    props: {
      modelValue: 'reverse_solvent', structureRef: 'str-real', solventOptions: [], candidateSystems: [],
      reverseRange: { target_rf: { min: 0, max: 1 }, ratio_step: { min: 0.1, max: 1 }, max_candidates: 20 }, ...mapped,
    },
    global: { stubs: { TlcCurveExplorer: { props: ['points', 'initialRf'], template: '<div data-testid="curve" />' } } },
  })
  return { wrapper, mapped }
}

const workspaceStructureInputStub = defineComponent({
  props: { projectId: { type: String, required: true }, targetId: { type: String, required: true } },
  emits: ['normalized', 'invalidated', 'draft-change'],
  setup(props, { emit }) {
    const workspace = inject(structureWorkspaceKey)!
    const smiles = ref('')
    let revision = 0
    const controller: StructureTargetController = {
      id: props.targetId,
      label: 'TLC 反推结构',
      projectId: props.projectId,
      disabled: ref(false),
      smiles,
      getSmiles: () => smiles.value,
      setSmiles(value) { smiles.value = value; emit('invalidated'); emit('draft-change', value) },
      invalidate() { emit('invalidated') },
      async normalize(): Promise<Structure | null> {
        const value = smiles.value
        if (!value.trim() || value === 'invalid') return null
        revision += 1
        const structure: Structure = {
          id: `structure-${revision}`, project_id: props.projectId, canonical_smiles: value,
          stereochemistry_status: 'specified', schema_version: '1.0', snapshot_hash: 'a'.repeat(64),
          created_at: '2026-10-20T00:00:00Z', is_valid: true, has_stereochemistry: true,
          original_input: { input_type: 'smiles', value }, stereochemistry_warnings: [],
        }
        emit('normalized', structure)
        return structure
      },
    }
    let unregister: (() => void) | undefined
    onMounted(() => { unregister = workspace.registerTarget(controller) })
    onBeforeUnmount(() => unregister?.())
    return () => h('div', [
      h('textarea', { 'data-testid': 'reverse-raw-smiles', value: smiles.value, onInput: (event: Event) => controller.setSmiles((event.target as HTMLTextAreaElement).value) }),
      h('button', { 'data-testid': 'normalize-current', onClick: () => { if (!smiles.value.trim()) controller.setSmiles('CCO'); void controller.normalize() } }, '规范化当前结构'),
      h('button', { 'data-testid': 'invalidate-current', onClick: () => controller.setSmiles('') }, '编辑结构'),
    ])
  },
})

const workspaceReversePanelStub = defineComponent({
  props: { structureRef: { type: String, default: null }, structureInputReady: Boolean, hideNavigation: Boolean, points: { type: Array, default: () => [] }, busy: Boolean },
  emits: ['submit-reverse'],
  setup(props, { emit }) {
    return () => h('section', [
      h('button', { 'data-testid': 'reverse-submit', disabled: !props.structureInputReady || props.busy, onClick: () => emit('submit-reverse', { target_rf: { min: 0.2, max: 0.3 }, candidate_system_ids: ['hexane/ethyl_acetate'], ratio_step: 0.5, max_candidates: 1 }) }, '提交反推'),
      h('span', { 'data-testid': 'curve-points' }, String(props.points.length)),
    ])
  },
})

function mountWorkspace(entry: 'prediction' | 'utilities') {
  const pinia = createPinia(); setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] })
  const app = useAppStore(pinia)
  app.accessContext = {
    actor: { id: 'actor', auth_mode: 'deployment_default', policy_version: 'policy-1' }, actor_id: 'actor', auth_mode: 'deployment_default',
    allowed_actions: ['*'], permissions: ['*'], is_multi_user_authenticated: false,
    project_selection: 'resolved', accessible_projects: [{ id: 'project-a', name: 'Project A' }], default_project_id: 'project-a',
  } as never
  app.state = 'ready'
  const requests: Array<{ url: string; method: string; body?: string }> = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method, body: typeof init?.body === 'string' ? init.body : undefined })
    if (url.endsWith('/prediction/types/tlc')) return new Response(JSON.stringify({ data: { id: 'tlc', display_name: 'TLC', tools: [{ id: 'reverse_solvent', schema_version: '1.0' }], models: [{ id: 'model-real', version: 'v1', task_type: 'tlc_reverse_solvent', scope: 'project', health: { status: 'READY', reason: null, checked_at: '' } }], limits: {}, health: { status: 'READY', reason: null, checked_at: '' } }, request_id: 'test', warnings: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
    if (url.includes('/prediction/types/tlc/tools/reverse_solvent/schema')) return new Response(JSON.stringify({ data: { id: 'reverse_solvent', schema_version: '1.0', input_schema: {}, output_schema: {}, structure_count: { min: 1, max: 1, semantics: 'single' }, model_constraints: {}, units: {}, supported_visualizations: [], limits: {}, health: { status: 'READY', reason: null, checked_at: '' } }, request_id: 'test', warnings: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
    if (url.endsWith('/tlc/conditions/options')) return new Response(JSON.stringify({ data: { solvent_options: [{ id: 'hexane' }, { id: 'ethyl_acetate' }], candidate_systems: [{ id: 'hexane/ethyl_acetate', solvent_ids: ['hexane', 'ethyl_acetate'] }], reverse: { target_rf: { min: 0, max: 1 }, ratio_step: { min: 0.5, max: 1 }, max_candidates: 5 } }, request_id: 'test', warnings: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
    if (url.endsWith('/tlc/reverse-solvent') && method === 'POST') return new Response(JSON.stringify({ data: { id: 'accepted-reverse', job_id: 'job-reverse', status: 'QUEUED', resource_ref: { type: 'prediction', id: 'accepted-reverse' } }, request_id: 'test', warnings: [] }), { status: 202, headers: { 'content-type': 'application/json' } })
    throw new Error(`Unexpected request ${method} ${url}`)
  }))
  const wrapper = mount(TlcReverseWorkspace, { props: { entry }, global: { plugins: [pinia, router], stubs: { RouterLink: true, StructureInput: workspaceStructureInputStub, TlcToolPanel: workspaceReversePanelStub } } })
  return { wrapper, requests, store: usePredictionStore(pinia) }
}

describe('TLC reverse results wired to prediction page', () => {
  it('blocks reverse submission for out-of-contract server capability metadata', async () => {
    const wrapper = mount(TlcToolPanel, {
      props: {
        modelValue: 'reverse_solvent', visibleTools: ['reverse_solvent'], structureRef: 'str-real',
        solventOptions: [], candidateSystems: [{ id: 'hexane/ethyl_acetate', solvent_ids: ['hexane', 'ethyl_acetate'] }],
        reverseRange: {
          target_rf: { min: 0, max: 1.01 }, ratio_step: { min: 0, max: 1.01 }, max_candidates: 201, available: true,
        },
      },
      global: { stubs: { TlcCurveExplorer: true } },
    })
    await wrapper.get('input[type="checkbox"]').setValue(true)
    expect(wrapper.get('button.primary').attributes('disabled')).toBeDefined()
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('submit-reverse')).toBeUndefined()
  })

  it('renders only real returned points and preserves their Rf and state', () => {
    const { wrapper, mapped } = renderReverse({
      ...baseResult,
      candidates: [
        { system_id: 'hexane/ethyl_acetate', mobile_phase: { ethyl_acetate: 0.25, hexane: 0.75 }, predicted_rf: 0.42, source: 'model' },
        { system_id: 'hexane/ethyl_acetate', mobile_phase: { ethyl_acetate: 0.5, hexane: 0.5 }, predicted_rf: 0.55, source: 'model' },
      ],
      evaluated: 4, target_reachable: true, warnings: [],
    })
    expect(mapped).toMatchObject({ initialRf: 0.5, resultState: 'success', failedPointCount: 0 })
    expect(mapped.points.map(({ x, rf, series }) => ({ x, rf, series }))).toEqual([
      { x: 0.25, rf: 0.42, series: 'hexane/ethyl_acetate' },
      { x: 0.5, rf: 0.55, series: 'hexane/ethyl_acetate' },
    ])
    expect(wrapper.get('.status').attributes('data-state')).toBe('success')
    expect(wrapper.text()).toContain('服务端正式结果与前端交互估计分开显示')
    expect(wrapper.find('[data-testid="curve"]').exists()).toBe(true)
  })

  it('shows the actual partial and total failure counts without turning them into successful points', () => {
    const partial = renderReverse({
      ...baseResult,
      candidates: [{ system_id: 'hexane/ethyl_acetate', mobile_phase: { ethyl_acetate: 0.5, hexane: 0.5 }, predicted_rf: 0.5, source: 'model' }],
      evaluated: 4, failed: 1, target_reachable: true, warnings: ['PARTIAL_FAILURE'],
    })
    expect(partial.wrapper.get('.status').attributes('data-state')).toBe('partial_failure')
    expect(partial.wrapper.text()).toContain('1 个扫描点失败')
    expect(partial.mapped.points).toHaveLength(1)
    expect(partial.mapped.points[0]?.rf).toBe(0.5)

    const failed = renderReverse({ ...baseResult, evaluated: 3, failed: 3, warnings: ['PARTIAL_FAILURE'] })
    expect(failed.wrapper.get('.status').attributes('data-state')).toBe('failed')
    expect(failed.wrapper.get('.status').text()).toBe('计算失败')
    expect(failed.mapped.points).toEqual([])
  })

  it('shows a genuine unreachable result with no invented curve points', () => {
    const { wrapper, mapped } = renderReverse(baseResult)
    expect(wrapper.get('.status').attributes('data-state')).toBe('unreachable')
    expect(wrapper.text()).toContain('目标 Rf 不在当前有效曲线可达范围内')
    expect(mapped.points).toEqual([])
    expect(mapped.resultMessage).toContain('TARGET_UNREACHABLE')
  })

  it('registers prediction and utilities entries to the same reverse workspace component', () => {
    const predictionRoute = predictionModule.routes.find((route) => route.path === '/prediction')
    const tlcRoute = predictionTlcExtension.routes.find((route) => route.path === '/prediction/tlc')
    const predictionReverse = predictionTlcReverseExtension.routes.find((route) => route.path === '/prediction/tlc/reverse')
    const utilityReverse = utilitiesTlcReverseExtension.routes.find((route) => route.path === '/utilities/tlc-reverse')
    expect(predictionRoute).toBeDefined()
    expect(tlcRoute).toBeDefined()
    expect(tlcRoute?.component).toBe(predictionRoute?.component)
    expect(predictionReverse?.component).toBe(TlcReverseWorkspace)
    expect(utilityReverse?.component).toBe(predictionReverse?.component)
    expect(utilityReverse?.meta).toMatchObject({ module: 'utilities', chromatographicType: 'tlc', predictionTool: 'reverse_solvent' })
  })

  it.each(['prediction', 'utilities'] as const)('%s entry presents formal reverse candidates in the same Chinese result table', async (entry) => {
    const { wrapper, store } = mountWorkspace(entry)
    await flushPromises()
    store.tlcOptions = {
      ...store.tlcOptions!,
      solvent_options: [{ id: 'hexane' }, { id: 'ethyl_acetate', label: '乙酸乙酯' }],
    } as never
    store.prediction = {
      id: 'prediction-reverse-display', project_id: 'project-a', chromatography_type: 'tlc', tool: 'reverse_solvent', status: 'SUCCEEDED',
      model: { id: 'model-real', version: 'v1' }, updated_at: '2026-10-08T00:00:00Z',
      warnings: [{ code: 'PREDICTION_NOTICE', message: '服务端任务提示' }],
      result: {
        ...baseResult,
        candidates: [
          { system_id: 'hexane/ethyl_acetate', mobile_phase: { ethyl_acetate: 0.25, hexane: 0.75 }, predicted_rf: 0.222364 },
          { system_id: 'petroleum_ether/ethyl_acetate', mobile_phase: { petroleum_ether: 0.6, ethyl_acetate: 0.4 }, predicted_rf: 0.321, source: 'model' },
          { system_id: 'dichloromethane/methanol', mobile_phase: { dichloromethane: 2, methanol: -1 }, predicted_rf: 0.5 },
          { system_id: 'hexane/ethyl_acetate', mobile_phase: {}, predicted_rf: 0.4 },
        ],
        evaluated: 4, failed: 0, target_reachable: true, warnings: ['候选结果警告'],
      },
    } as unknown as ContractPrediction
    await nextTick()
    await flushPromises()

    expect(wrapper.findAll('.reverse-result-table thead th').map((cell) => cell.text())).toEqual(['候选体系', '展开剂（体积分数）', 'Rf', '结果来源'])
    expect(wrapper.get('[role="region"][aria-label="正式反推候选结果"]').attributes('tabindex')).toBe('0')
    expect(wrapper.findAll('.reverse-result-table tbody tr td').map((cell) => cell.text())).toEqual([
      'hexane/ethyl_acetate', '乙酸乙酯 25.00%；hexane 75.00%', '0.22', '—',
      'petroleum_ether/ethyl_acetate', 'petroleum_ether 60.00%；乙酸乙酯 40.00%', '0.32', 'model',
      'dichloromethane/methanol', 'dichloromethane —；methanol —', '0.50', '—',
      'hexane/ethyl_acetate', '—', '0.40', '—',
    ])
    expect(wrapper.findAll('.event-list p').map((item) => item.text())).toEqual(['服务端任务提示', '候选结果警告'])
    expect(wrapper.get('.reverse-result-table').text()).not.toContain('{')
    expect(wrapper.get('.reverse-result-table').text()).not.toContain('"')
    wrapper.unmount()
  })

  it('uses the same store POST and restores the same formal result for either entry', async () => {
    const body: ContractTlcReverseBody = {
      project_id: 'project-1', schema_version: '1.0', model_selection: { mode: 'exact', model_id: 'model-1', model_version: 'v1' },
      structure_ref: 'structure-1', target_rf_min: 0.22, target_rf_max: 0.23, candidate_system_ids: ['hexane/ethyl_acetate'],
      ratio_step: 0.5, max_candidates: 1, chromatography_type: 'tlc', tool: 'reverse_solvent',
    }
    const prediction = { id: 'pred-reverse', project_id: 'project-1', chromatography_type: 'tlc', tool: 'reverse_solvent', status: 'SUCCEEDED', result: { ...baseResult, target_reachable: true, candidates: [{ predicted_rf: 0.222364 }], warnings: [] } } as unknown as ContractPrediction
    const requests: Array<{ url: string; method: string }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      const data = method === 'POST' ? { id: prediction.id, status: 'QUEUED' } : prediction
      return new Response(JSON.stringify({ data, request_id: 'test', warnings: [] }), { status: method === 'POST' ? 202 : 200, headers: { 'content-type': 'application/json' } })
    })
    const store = usePredictionStore()
    await store.createTlcReverse(body)
    await expect(store.getPrediction(prediction.id, () => true, 'project-1', 'tlc', (loaded) => loaded.id === prediction.id && loaded.tool === 'reverse_solvent')).resolves.toMatchObject({ id: prediction.id, status: 'SUCCEEDED' })
    expect(store.prediction?.result).toMatchObject({ task_type: 'tlc_reverse_solvent', candidates: [{ predicted_rf: 0.222364 }] })
    expect(requests).toEqual([
      { url: '/api/v1/tlc/reverse-solvent', method: 'POST' },
      { url: '/api/v1/predictions/pred-reverse', method: 'GET' },
    ])
  })

  it('converts ratio notation through the mounted panel without silently normalizing totals', async () => {
    const mountSingle = () => mount(TlcToolPanel, {
      props: {
        modelValue: 'single', visibleTools: ['single'], structureRef: 'str-real',
        solventOptions: [{ id: 'hexane' }, { id: 'ethyl_acetate' }], candidateSystems: [],
        reverseRange: { target_rf: { min: 0, max: 1 }, ratio_step: { min: 0.1, max: 1 }, max_candidates: 20 },
      },
    })
    const wrapper = mountSingle()
    const inputs = wrapper.findAll('input[inputmode="decimal"]')
    await inputs[0]!.setValue('8:2')
    await inputs[1]!.setValue('2:8')
    expect((wrapper.find('.primary').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.find('.primary').trigger('click')
    expect(wrapper.emitted('submit-single')?.at(-1)?.[0]).toEqual({
      solvent_components: [
        { solvent_id: 'hexane', fraction: 0.8 },
        { solvent_id: 'ethyl_acetate', fraction: 0.2 },
      ],
    })
    await inputs[1]!.setValue('80:20')
    expect((wrapper.find('.primary').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.emitted('submit-single')).toHaveLength(1)
    wrapper.unmount()

    const scaled = mountSingle()
    const scaledInputs = scaled.findAll('input[inputmode="decimal"]')
    await scaledInputs[0]!.setValue('80:20')
    await scaledInputs[1]!.setValue('20:80')
    await scaled.find('.primary').trigger('click')
    expect(scaled.emitted('submit-single')?.[0]?.[0]).toEqual({
      solvent_components: [
        { solvent_id: 'hexane', fraction: 0.8 },
        { solvent_id: 'ethyl_acetate', fraction: 0.2 },
      ],
    })
    scaled.unmount()
  })

  it('mounts the real curve canvas, keeps each solvent-system line intact, and clamps Rf dragging', async () => {
    const contexts = mockCanvasContexts()
    const wrapper = mount(TlcToolPanel, {
      props: {
        modelValue: 'reverse_solvent', visibleTools: ['reverse_solvent'], structureRef: 'str-real',
        solventOptions: [], candidateSystems: [],
        reverseRange: { target_rf: { min: 0, max: 1 }, ratio_step: { min: 0.1, max: 1 }, max_candidates: 20 },
        initialRf: 0.5, resultState: 'success',
        points: [
          { x: 0.2, ratio: 0.2, rf: 0.2, series: 'hexane/ethyl_acetate' },
          { x: 0.8, ratio: 0.8, rf: 0.8, series: 'hexane/ethyl_acetate' },
          { x: 0.3, ratio: 0.3, rf: 0.2, series: 'dichloromethane/methanol' },
          { x: 0.7, ratio: 0.7, rf: 0.8, series: 'dichloromethane/methanol' },
        ],
      },
    })
    const explorer = wrapper.get('.tlc-explorer')
    expect(explorer.text()).toContain('曲线存在 2 个可能交点')
    const curveCanvas = explorer.get('canvas[aria-label="Rf 曲线"]').element as HTMLCanvasElement
    const curveStrokes = contexts.get(curveCanvas)!.strokes.filter((stroke) => stroke.style === '#0f6e56' && stroke.path.length === 2)
    expect(new Set(curveStrokes.map((stroke) => stroke.path.map(([x]) => x).join(':'))).size).toBe(2)

    const plate = explorer.get('canvas[role="slider"]')
    Object.defineProperty(plate.element, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ x: 0, y: 10, top: 10, left: 0, right: 400, bottom: 340, width: 400, height: 330, toJSON() {} }),
    })
    await plate.trigger('pointerdown', { button: 0, pointerId: 1, clientY: 10 })
    expect(plate.attributes('aria-valuenow')).toBe('0.80')
    await plate.trigger('pointermove', { button: 0, pointerId: 1, clientY: 340 })
    expect(plate.attributes('aria-valuenow')).toBe('0.20')
    expect(wrapper.emitted('rf-change')?.flat()).toEqual([0.8, 0.2])
    wrapper.unmount()
  })

  it('mounts failed scan evidence without drawing/interpolating across the gap', () => {
    const contexts = mockCanvasContexts()
    const wrapper = mount(TlcToolPanel, {
      props: {
        modelValue: 'reverse_solvent', visibleTools: ['reverse_solvent'], structureRef: 'str-real',
        solventOptions: [], candidateSystems: [],
        reverseRange: { target_rf: { min: 0, max: 1 }, ratio_step: { min: 0.1, max: 1 }, max_candidates: 20 },
        resultState: 'partial_failure', failedPointCount: 1,
        points: [
          { x: 0.2, ratio: 0.2, rf: 0.2, series: 'hexane/ethyl_acetate' },
          { x: 0.5, ratio: 0.5, rf: null, series: 'hexane/ethyl_acetate' },
          { x: 0.8, ratio: 0.8, rf: 0.8, series: 'hexane/ethyl_acetate' },
        ],
      },
    })
    expect(wrapper.get('.status').attributes('data-state')).toBe('partial_failure')
    expect(wrapper.text()).toContain('1 个扫描点失败')
    const curve = wrapper.get('canvas[aria-label="Rf 曲线"]').element as HTMLCanvasElement
    const greenSegments = contexts.get(curve)!.strokes.filter((stroke) => stroke.style === '#0f6e56' && stroke.path.length === 2)
    expect(greenSegments).toEqual([])
    expect(wrapper.get('.warning-text').text()).toContain('1 个扫描点失败')
    wrapper.unmount()
  })

  it.each(['prediction', 'utilities'] as const)('%s entry drops the old structure reference on edit while preserving the completed curve', async (entry) => {
    const { wrapper, requests, store } = mountWorkspace(entry)
    await flushPromises()
    const completedPrediction = {
      id: 'prediction-completed', project_id: 'project-a', chromatography_type: 'tlc', tool: 'reverse_solvent', status: 'SUCCEEDED',
      model: { id: 'model-real', version: 'v1' }, updated_at: '2026-10-07T00:00:00Z',
      input_snapshot: { input: { target_rf_min: 0.4, target_rf_max: 0.6, ratio_step: 0.25 } },
      result: { ...baseResult, candidates: [{ system_id: 'hexane/ethyl_acetate', mobile_phase: { ethyl_acetate: 0.5, hexane: 0.5 }, predicted_rf: 0.52, source: 'model' }], evaluated: 1, failed: 0, target_reachable: true, warnings: [] },
    } as unknown as ContractPrediction
    store.prediction = completedPrediction

    await wrapper.get('[data-testid="normalize-current"]').trigger('click')
    expect(wrapper.get('[data-testid="reverse-submit"]').element).toHaveProperty('disabled', false)
    expect(wrapper.get('[data-testid="curve-points"]').text()).toBe('1')
    expect(wrapper.get('.result-panel').text()).toContain('0.52')

    await wrapper.get('[data-testid="invalidate-current"]').trigger('click')
    expect(wrapper.get('[data-testid="reverse-submit"]').element).toHaveProperty('disabled', true)
    expect(wrapper.findComponent(workspaceReversePanelStub).props('structureRef')).toBe(null)
    expect(wrapper.get('[data-testid="curve-points"]').text()).toBe('1')
    expect(wrapper.get('.result-panel').text()).toContain('0.52')
    expect(store.prediction).toMatchObject({ id: 'prediction-completed', project_id: 'project-a' })
    await wrapper.get('[data-testid="reverse-raw-smiles"]').setValue('C[C@H](O)C')
    expect(wrapper.get('[data-testid="reverse-submit"]').element).toHaveProperty('disabled', false)
    await wrapper.get('[data-testid="reverse-submit"]').trigger('click')
    await flushPromises()
    const post = requests.find((request) => request.method === 'POST' && request.url.includes('/tlc/reverse-solvent'))
    expect(JSON.parse(post?.body ?? '{}')).toMatchObject({ structure_ref: 'structure-2' })
    wrapper.unmount()
  })

  it('automatically validates the raw structure on reverse submit and never posts when validation fails', async () => {
    const { wrapper, requests } = mountWorkspace('prediction')
    await flushPromises()
    expect(wrapper.findAll('.tlc-tool-nav button').map((button) => button.text())).toEqual(['单次预测', '批量预测', '条件比较', '反推溶剂'])
    expect(wrapper.findComponent(workspaceReversePanelStub).props('hideNavigation')).toBe(true)
    await wrapper.get('[data-testid="reverse-raw-smiles"]').setValue('invalid')
    expect(wrapper.get('[data-testid="reverse-submit"]').element).toHaveProperty('disabled', false)
    await wrapper.get('[data-testid="reverse-submit"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('结构校验未通过')
    expect(requests.some((request) => request.method === 'POST' && request.url.includes('/tlc/reverse-solvent'))).toBe(false)
    wrapper.unmount()
  })

  it('submits a raw TLC structure after automatic normalization without a manual normalize click', async () => {
    const { wrapper, requests } = mountWorkspace('prediction')
    await flushPromises()
    await wrapper.get('[data-testid="reverse-raw-smiles"]').setValue('CCO')

    await wrapper.get('[data-testid="reverse-submit"]').trigger('click')
    await flushPromises()

    const post = requests.find((request) => request.method === 'POST' && request.url.endsWith('/tlc/reverse-solvent'))
    expect(post).toBeTruthy()
    expect(JSON.parse(post!.body ?? '{}').structure_ref).toBe('structure-1')
    wrapper.unmount()
  })
})
