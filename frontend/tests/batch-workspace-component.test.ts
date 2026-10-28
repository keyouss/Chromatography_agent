import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import BatchWorkspace from '../src/shared/batch/BatchWorkspace.vue'
import type {
  BatchDraft,
  BatchRowResult,
  BatchChromatographyType,
  BatchTypeCapability,
  NonTlcBatchToolCapability,
  TlcBatchToolCapability,
} from '../src/shared/batch/batch-types'
import type { Artifact } from '../src/core/types'

const health = { status: 'READY', checked_at: '2026-10-22T00:00:00Z', reason: null } as const

function response(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, request_id: 'batch-workspace-test', warnings: [] }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function batchDraft(overrides: Partial<BatchDraft> = {}): BatchDraft {
  return {
    id: 'batch-record-1', project_id: 'project-a', chromatography_type: 'tlc', tool: 'batch',
    config_hash: 'a'.repeat(64), draft_status: 'READY', execution_status: 'SUCCEEDED', job_id: 'job-1',
    counts: { total: 1, queued: 0, running: 0, succeeded: 1, failed: 0, skipped: 0, canceled: 0 }, progress: 1,
    revision: 1, etag: '"batch-r1"', name: 'TLC history', schema_version: '1.1',
    model_selection: { mode: 'champion', scope_id: 'scope-tlc' }, input_artifact_id: 'artifact-input-1',
    template_version: 'tlc-batch-2', field_mapping: {}, common_input: {}, invalid_row_policy: 'block',
    resource_budget: { max_evaluations: 10, max_runtime_seconds: 60 }, ...overrides,
  }
}

function typeCapability(models: BatchTypeCapability['models'] = [], tools: BatchTypeCapability['tools'] = [], type = 'tlc'): BatchTypeCapability {
  return {
    id: type, display_name: type.toUpperCase(), health,
    limits: { max_rows: 100, max_candidates: 10, max_evaluations: 100 }, models, tools,
  }
}

function artifact(projectId = 'project-a'): Artifact {
  return {
    id: 'artifact-input-1', project_id: projectId, kind: 'batch_input', status: 'AVAILABLE', scan_status: 'PASSED',
    file_name: 'input.csv', media_type: 'text/csv', size_bytes: 20, sha256: 'b'.repeat(64), owner_id: 'actor-a',
    created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z', download_allowed: false,
  }
}

function rowResult(status: BatchRowResult['status'] = 'SUCCEEDED'): BatchRowResult {
  return {
    row_id: 'row-1', source_row_number: 2, attempt: 1, status, input_snapshot: { structure_ref: 'structure-1' },
    result: status === 'SUCCEEDED' ? { task_type: 'tlc', rows: [{ rf: 0.5 }] } : null,
    error: status === 'FAILED' ? { message: 'execution failed' } : null, warnings: [],
  }
}

function legacyModel() {
  return {
    id: 'model-tlc-1', version: 'v1', input_schema_version: '1.1', output_schema_version: '1.1',
    scope: 'TLC scope', task_type: 'tlc', health,
  } satisfies BatchTypeCapability['models'][number]
}

function legacyTool(): TlcBatchToolCapability {
  return {
    id: 'batch', chromatography_type: 'tlc', schema_version: '1.0', template_version: 'tlc-batch-1', health,
    limits: { max_evaluations: 10, max_runtime_seconds: 60 }, input_schema: {}, output_schema: {},
    model_constraints: {
      template_versions: ['tlc-batch-1'],
      compatible_models: [{ model_id: 'model-tlc-1', model_version: 'v1', input_schema_version: '1.1',
        template_versions: ['tlc-batch-1'], health }],
    },
    structure_count: { min: 1, max: 1, semantics: 'batch' }, supported_visualizations: [], units: {},
    templates: [{ id: 'tlc-batch', chromatography_type: 'tlc', tool: 'batch', version: 'tlc-batch-1',
      row_schema: 'TlcBatchRowV1', fields: ['structure_ref', 'solvent_1'], limits: { max_evaluations: 10, max_runtime_seconds: 60 }, health }],
  }
}

function typedBatchFixtures(type: BatchChromatographyType): {
  model: BatchTypeCapability['models'][number]
  tool: TlcBatchToolCapability | NonTlcBatchToolCapability
  capability: BatchTypeCapability
  templateVersion: string
} {
  const templateVersion = ({ tlc: 'tlc-batch-2', cc: 'cc-batch-2', hplc: 'hplc-batch-2', gc: 'gc-batch-1', sfc: 'sfc-batch-2' } as const)[type]
  const modelSchemaVersion = type === 'hplc' || type === 'gc' || type === 'sfc' ? '1.1' : '1.0'
  const usesProfile = type === 'cc' || type === 'hplc' || type === 'gc'
  const profile = usesProfile ? { preprocessing_profile_id: `profile-${type}`, preprocessing_profile_sha256: 'c'.repeat(64) } : {}
  const model = {
    id: `model-${type}`, version: 'v1', input_schema_version: modelSchemaVersion, output_schema_version: '1.0',
    scope: `${type} scope`, task_type: type, health, ...profile,
  } satisfies BatchTypeCapability['models'][number]
  const fields = ({
    tlc: ['smiles', 'solvent_1', 'fraction_1', 'solvent_2', 'fraction_2'],
    cc: ['structure_ref_a', 'structure_ref_b', 'eluent_system_id', 'loading_solvent_id', 'sample_mass_mg', 'loading_solvent_volume_ul', 'flow_rate_ml_min', 'column_spec_id'],
    hplc: ['smiles_a', 'smiles_b', 'isopropanol_fraction', 'flow_rate_ml_min', 'column_ids'],
    gc: ['structure_ref', 'temperature_program'],
    sfc: ['structure_ref_a', 'structure_ref_b', 'modifier_total_fraction', 'modifier_components', 'candidate_scope_id', 'top_k', 'tool', 'column_id'],
  } as const)[type]
  const compatible = {
    model_id: model.id, model_version: model.version, input_schema_version: model.input_schema_version,
    template_versions: [templateVersion], health,
    ...(type === 'tlc' ? {} : { tools: [type === 'sfc' ? 'single_column' : 'single'], ...profile }),
  }
  const tool = {
    id: 'batch', chromatography_type: type, schema_version: '1.0', template_version: templateVersion, health,
    limits: { max_evaluations: 100, max_runtime_seconds: 3600 }, input_schema: {}, output_schema: {},
    model_constraints: { template_versions: [templateVersion], compatible_models: [compatible] },
    structure_count: { min: 1, max: 2, semantics: 'batch' }, supported_visualizations: [], units: {},
    templates: [{ id: `${type}-batch`, chromatography_type: type, tool: 'batch', version: templateVersion,
      row_schema: `${type}BatchRow`, fields: [...fields], limits: { max_evaluations: 100, max_runtime_seconds: 3600 }, health }],
  } as unknown as TlcBatchToolCapability | NonTlcBatchToolCapability
  const capability = typeCapability([model], [tool], type)
  return { model, tool, capability, templateVersion }
}

function writableTlcFixtures() {
  const model = { ...legacyModel(), output_schema_version: '1.1' }
  const tool = legacyTool()
  tool.template_version = 'tlc-batch-2'
  tool.model_constraints.template_versions = ['tlc-batch-2']
  tool.model_constraints.compatible_models[0]!.template_versions = ['tlc-batch-2']
  tool.templates[0]!.version = 'tlc-batch-2'
  tool.templates[0]!.limits = tool.limits
  const capability = typeCapability([model], [tool])
  const draft = batchDraft({
    draft_status: 'READY', execution_status: 'SUCCEEDED', job_id: 'job-validation', validation_id: 'validation-1',
    model_selection: { mode: 'exact', model_id: model.id, model_version: model.version },
    template_version: 'tlc-batch-2',
  })
  const validation = {
    id: 'validation-1', status: 'READY', draft_revision: draft.revision, config_hash: draft.config_hash,
    template_version: draft.template_version, total_rows: 0, valid_rows: 0, invalid_rows: 0, excluded_rows: 0,
    total_evaluations: 0, resource_estimate: { max_evaluations: 10, max_runtime_seconds: 60 },
    model_snapshot: { id: model.id, version: model.version, input_schema_version: model.input_schema_version,
      output_schema_version: model.output_schema_version },
  }
  const job = {
    id: 'job-validation', project_id: draft.project_id, resource_ref: { type: 'batch_validation', id: validation.id },
    status: 'SUCCEEDED', kind: 'batch_validation', attempt: 1, cancelable: false,
    created_at: '2026-10-22T00:00:00Z', updated_at: '2026-10-22T00:00:00Z', deadline_at: null,
    error: null, etag: '"job-1"', finished_at: '2026-10-22T00:00:01Z', owner_id: 'actor-a', progress: {},
    result_ref: null, resume_mode: 'none', retryable: false, revision: 1, started_at: '2026-10-22T00:00:00Z',
  }
  return { model, tool, capability, draft, validation, job }
}

function mountedWorkspace(fetchMock: ReturnType<typeof vi.fn>, props: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', fetchMock)
  setActivePinia(createPinia())
  return mount(BatchWorkspace, {
    props: {
      actorId: 'actor-a', projectId: 'project-a', chromatographyType: 'tlc',
      canReadBatch: true, canUploadBatch: false, canCreateBatch: false, canCancelBatch: false,
      ...props,
    },
    global: { stubs: { JobStatus: true } },
  })
}

beforeEach(() => {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
  })
  setActivePinia(createPinia())
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('BatchWorkspace record recovery', () => {
  it.each(['tlc', 'cc', 'hplc', 'gc', 'sfc'] as const)(
    'separates the %s batch-tool schema from its model input schema', async (type) => {
      const { model, tool, capability, templateVersion } = typedBatchFixtures(type)
      const requests: string[] = []
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input); requests.push(url)
        if (url.endsWith(`/prediction/types/${type}`)) return response(capability)
        if (url.includes('/tools/batch/schema?')) return response(tool)
        throw new Error(`Unexpected ${type} schema request: ${url}`)
      })
      const wrapper = mountedWorkspace(fetchMock, { chromatographyType: type })
      await flushPromises()
      const select = wrapper.get('[data-testid="batch-model-select"]')
      await select.setValue(select.findAll('option')[1]!.attributes('value'))
      await flushPromises()

      expect(requests.some((url) => url.includes(`/tools/batch/schema?schema_version=1.0`))).toBe(true)
      expect(wrapper.get('[data-testid="batch-tool-ready"]').text()).toContain(templateVersion)
      expect((wrapper.get('[data-testid="download-batch-template"]').element as HTMLButtonElement).disabled).toBe(false)
      expect(model.input_schema_version).toBe(type === 'hplc' || type === 'gc' || type === 'sfc' ? '1.1' : '1.0')
      wrapper.unmount()
    },
  )

  it('keeps the HPLC model input schema in the created draft payload', async () => {
    const { model, tool, capability, templateVersion } = typedBatchFixtures('hplc')
    const templateFields = tool.templates[0]!.fields
    const draft = batchDraft({
      id: 'batch-hplc-1', project_id: 'project-a', chromatography_type: 'hplc', tool: 'batch', schema_version: '1.1',
      model_selection: { mode: 'exact', model_id: model.id, model_version: model.version,
        preprocessing_profile_id: model.preprocessing_profile_id!, preprocessing_profile_sha256: model.preprocessing_profile_sha256! },
      input_artifact_id: 'artifact-input-1', template_version: templateVersion,
      field_mapping: Object.fromEntries(templateFields.map((field) => [field, field])),
      revision: 1, etag: '"hplc-r1"', config_hash: 'a'.repeat(64), job_id: undefined,
      execution_status: undefined, validation_id: undefined,
    })
    const draftBodies: Record<string, unknown>[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/prediction/types/hplc')) return response(capability)
      if (url.includes('/tools/batch/schema?')) return response(tool)
      if (url.includes('/batch-template?')) return new Response(new Blob(['smiles_a,smiles_b']), {
        status: 200, headers: { 'content-type': 'text/csv', 'X-Template-Version': templateVersion, 'X-Template-SHA256': 'd'.repeat(64) },
      })
      if (url.endsWith('/artifacts/uploads') && init?.method === 'POST') return response({
        id: 'upload-1', artifact_id: 'artifact-input-1', status: 'CREATED', content_url: '/artifacts/uploads/upload-1/content',
        allowed_media_types: ['text/csv'], max_size_bytes: 1000, expires_at: '2026-10-23T00:00:00Z', received_bytes: 0,
      })
      if (url.endsWith('/artifacts/uploads/upload-1/content')) return new Response(null, { status: 204 })
      if (url.endsWith('/artifacts/uploads/upload-1/complete')) return response({
        id: 'artifact-input-1', artifact_id: 'artifact-input-1', job_id: 'job-upload', status: 'VALIDATING',
        resource_ref: { type: 'artifact', id: 'artifact-input-1' },
      })
      if (url.endsWith('/artifacts/artifact-input-1')) return response(artifact())
      if (url.endsWith('/batch-predictions/drafts') && init?.method === 'POST') {
        draftBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return response(draft)
      }
      throw new Error(`Unexpected HPLC draft request: ${init?.method ?? 'GET'} ${url}`)
    })
    vi.stubGlobal('URL', { createObjectURL: () => 'blob:batch-template', revokeObjectURL: () => undefined })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const wrapper = mountedWorkspace(fetchMock, {
      chromatographyType: 'hplc', canUploadBatch: true, canCreateBatch: true,
    })
    await flushPromises()
    const select = wrapper.get('[data-testid="batch-model-select"]')
    await select.setValue(select.findAll('option')[1]!.attributes('value'))
    await flushPromises()
    await wrapper.get('[data-testid="download-batch-template"]').trigger('click')
    await flushPromises()
    const file = new File(['smiles_a,smiles_b\nC,CC\n'], 'hplc.csv', { type: 'text/csv' })
    const fileInput = wrapper.get('[data-testid="batch-file-input"]')
    Object.defineProperty(fileInput.element, 'files', { configurable: true, value: [file] })
    await fileInput.trigger('change')
    await wrapper.get('[data-testid="upload-batch-file"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="create-batch-draft"]').trigger('click')
    await flushPromises()

    expect(draftBodies[0]?.schema_version).toBe('1.1')
    expect(draftBodies[0]?.template_version).toBe(templateVersion)
    expect(draftBodies[0]?.model_selection).toMatchObject({ model_id: model.id, model_version: model.version,
      preprocessing_profile_id: model.preprocessing_profile_id, preprocessing_profile_sha256: model.preprocessing_profile_sha256 })
    wrapper.unmount()
  })

  it('rejects batch advertisements without one exact model and profile binding', async () => {
    const { model, tool, capability } = typedBatchFixtures('hplc')
    const advertised = capability.tools[0] as NonTlcBatchToolCapability
    advertised.model_constraints.compatible_models[0]!.model_version = 'other-version'
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/prediction/types/hplc')) return response(capability)
      if (url.includes('/tools/batch/schema?')) return response(tool)
      throw new Error(`Unexpected incompatible batch request: ${url}`)
    })
    const wrapper = mountedWorkspace(fetchMock, { chromatographyType: 'hplc' })
    await flushPromises()
    const select = wrapper.get('[data-testid="batch-model-select"]')
    await select.setValue(select.findAll('option')[1]!.attributes('value'))
    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/tools/batch/schema?'))).toBe(false)
    expect(wrapper.get('[data-testid="batch-tool-blocked"]').text()).toContain('唯一批量兼容能力')
    expect(model.input_schema_version).toBe('1.1')
    wrapper.unmount()
  })

  it('rejects a fetched batch-tool schema that differs from its advertised envelope schema', async () => {
    const { tool, capability } = typedBatchFixtures('gc')
    const mismatchedTool = { ...tool, schema_version: '2.0' }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/prediction/types/gc')) return response(capability)
      if (url.includes('/tools/batch/schema?')) return response(mismatchedTool)
      throw new Error(`Unexpected mismatched batch request: ${url}`)
    })
    const wrapper = mountedWorkspace(fetchMock, { chromatographyType: 'gc' })
    await flushPromises()
    const select = wrapper.get('[data-testid="batch-model-select"]')
    await select.setValue(select.findAll('option')[1]!.attributes('value'))
    await flushPromises()

    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/tools/batch/schema?schema_version=1.0'))).toBe(true)
    expect(wrapper.find('[data-testid="batch-tool-ready"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="batch-tool-blocked"]').text()).toContain('工具 schema')
    wrapper.unmount()
  })

  it('shows the selected batch budget instead of single-prediction limits', async () => {
    const tool = legacyTool()
    tool.template_version = 'tlc-batch-2'
    tool.limits = { max_evaluations: 100000, max_runtime_seconds: 3600 }
    tool.model_constraints.template_versions = ['tlc-batch-2']
    tool.model_constraints.compatible_models[0]!.template_versions = ['tlc-batch-2']
    tool.templates[0]!.version = 'tlc-batch-2'
    tool.templates[0]!.limits = tool.limits
    const capability = typeCapability([legacyModel()], [tool])
    capability.limits = { max_rows: 1, max_candidates: 1, max_evaluations: 1 }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/prediction/types/tlc')) return response(capability)
      if (url.includes('/tools/batch/schema?')) return response(tool)
      throw new Error(`Unexpected batch budget request: ${url}`)
    })
    const wrapper = mountedWorkspace(fetchMock)
    await flushPromises()
    const select = wrapper.get('[data-testid="batch-model-select"]')
    const option = select.findAll('option')[1]!
    await select.setValue(option.attributes('value')); await flushPromises()
    expect(wrapper.get('[data-testid="batch-tool-limits"]').text()).toContain('100000 次计算')
    expect(wrapper.get('[data-testid="batch-tool-limits"]').text()).toContain('3600 秒')
    expect(wrapper.text()).not.toContain('最多 1 行')
    wrapper.unmount()
  })

  it('explains a quarantined artifact even when its scan has passed', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/prediction/types/tlc')) return response(typeCapability())
      if (url.endsWith('/batch-predictions/batch-record-1')) return response(batchDraft())
      if (url.endsWith('/artifacts/artifact-input-1')) return response({ ...artifact(), status: 'QUARANTINED' })
      if (url.includes('/batch-predictions/batch-record-1/rows')) return response({ rows: [], has_more: false })
      throw new Error(`Unexpected quarantined artifact request: ${url}`)
    })
    const wrapper = mountedWorkspace(fetchMock, { initialBatchId: 'batch-record-1' })
    await flushPromises()
    expect(wrapper.get('[data-testid="batch-artifact-blocker"]').text()).toContain('扫描已通过，但制品状态为 QUARANTINED')
    expect(wrapper.get('[data-testid="batch-artifact-blocker"]').text()).toContain('不能用于预测')
    expect(wrapper.text()).not.toContain('扫描完成前不会用于预测')
    expect((wrapper.get('[data-testid="create-batch-draft"]').element as HTMLButtonElement).disabled).toBe(true)
    wrapper.unmount()
  })

  it('restores the requested batch using GET only and reads its artifact and rows', async () => {
    const requests: Array<{ url: string; method: string }> = []
    const saved = batchDraft()
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url.endsWith('/prediction/types/tlc')) return response(typeCapability())
      if (url.endsWith('/batch-predictions/batch-record-1')) return response(saved)
      if (url.endsWith('/artifacts/artifact-input-1')) return response(artifact())
      if (url.includes('/batch-predictions/batch-record-1/rows')) return response({ rows: [rowResult()], has_more: false })
      throw new Error(`Unexpected batch recovery request: ${method} ${url}`)
    })

    const wrapper = mountedWorkspace(fetchMock, { initialBatchId: 'batch-record-1' })
    await flushPromises()

    expect(wrapper.get('.draft-state').text()).toContain('tlc-batch-2')
    expect(wrapper.get('[aria-label="批量执行结果"]').text()).toContain('第 2 行')
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    expect(requests.some((request) => request.url.endsWith('/artifacts/artifact-input-1'))).toBe(true)
    expect(requests.some((request) => request.url.includes('/batch-record-1/rows'))).toBe(true)
    wrapper.unmount()
  })

  it('rejects a record bound to another project before reading artifact or result rows', async () => {
    const requests: Array<{ url: string; method: string }> = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url.endsWith('/prediction/types/tlc')) return response(typeCapability())
      if (url.endsWith('/batch-predictions/batch-record-1')) return response(batchDraft({ project_id: 'project-other' }))
      throw new Error(`Unexpected mismatched-record request: ${method} ${url}`)
    })

    const wrapper = mountedWorkspace(fetchMock, { initialBatchId: 'batch-record-1' })
    await flushPromises()

    expect(wrapper.get('[data-testid="batch-operation-error"]').text()).toContain('项目')
    expect(requests.some((request) => request.url.includes('/artifacts/'))).toBe(false)
    expect(requests.some((request) => request.url.includes('/rows'))).toBe(false)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it('shows submitted TLC v1 results read-only and keeps validation and retry actions closed', async () => {
    const requests: Array<{ url: string; method: string }> = []
    const tool = legacyTool()
    const draft = batchDraft({
      model_selection: { mode: 'exact', model_id: 'model-tlc-1', model_version: 'v1' },
      template_version: 'tlc-batch-1', execution_status: 'FAILED', job_id: 'job-legacy',
      counts: { total: 1, queued: 0, running: 0, succeeded: 0, failed: 1, skipped: 0, canceled: 0 },
    })
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url.endsWith('/prediction/types/tlc')) return response(typeCapability([legacyModel()], [tool]))
      if (url.includes('/tools/batch/schema?')) return response(tool)
      if (url.endsWith('/batch-predictions/batch-record-1')) return response(draft)
      if (url.endsWith('/artifacts/artifact-input-1')) return response(artifact())
      if (url.includes('/batch-predictions/batch-record-1/rows')) return response({ rows: [rowResult('FAILED')], has_more: false })
      throw new Error(`Unexpected legacy recovery request: ${method} ${url}`)
    })

    const wrapper = mountedWorkspace(fetchMock, { initialBatchId: 'batch-record-1', canCreateBatch: true })
    await flushPromises()

    expect(wrapper.find('[data-testid="legacy-template-readonly"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="批量执行结果"]').text()).toContain('FAILED')
    expect((wrapper.get('[aria-label="批量草稿与服务端校验"] button').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.findAll('[data-testid="submit-batch"]')).toHaveLength(0)
    expect((wrapper.get('.retry-panel button').element as HTMLButtonElement).disabled).toBe(true)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it('allows a successfully completed validation job to reach the normal confirmation flow', async () => {
    const { capability, tool, draft, validation, job } = writableTlcFixtures()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/prediction/types/tlc')) return response(capability)
      if (url.includes('/tools/batch/schema?')) return response(tool)
      if (url.endsWith('/batch-predictions/batch-record-1')) return response(draft)
      if (url.endsWith('/artifacts/artifact-input-1')) return response(artifact())
      if (url.endsWith('/batch-predictions/batch-record-1/validation')) return response(validation)
      if (url.includes('/validation/rows')) return response({ rows: [], has_more: false })
      if (url.includes('/batch-predictions/batch-record-1/rows')) return response({ rows: [], has_more: false })
      if (url.endsWith('/jobs/job-validation')) return response(job)
      if (url.endsWith('/confirmations/previews')) return response({
        resource_ref: { type: 'batch_prediction', id: draft.id }, action: 'batch.submit', can_confirm: true,
        revision: draft.revision, etag: draft.etag, config_hash: draft.config_hash, summary: [], dependencies: [],
        impact: {}, blockers: [], policy_version: 'policy-1',
      })
      if (url.endsWith('/confirmations')) return response({
        id: 'confirmation-1', resource_ref: { type: 'batch_prediction', id: draft.id }, action: 'batch.submit',
        project_id: draft.project_id, revision: draft.revision, config_hash: draft.config_hash,
        policy_version: 'policy-1', status: 'CONFIRMED',
      })
      throw new Error(`Unexpected validation recovery request: ${url}`)
    })

    const wrapper = mountedWorkspace(fetchMock, { initialBatchId: draft.id, canCreateBatch: true })
    await flushPromises()
    expect(wrapper.find('[aria-label="批量提交确认"]').exists()).toBe(true)
    await wrapper.get('[aria-label="批量提交确认"] button').trigger('click')
    await flushPromises()
    const confirmButton = wrapper.findAll('button').find((button) => button.text() === '确认此版本')
    expect(confirmButton).toBeDefined()
    expect((confirmButton!.element as HTMLButtonElement).disabled).toBe(false)
    await confirmButton!.trigger('click')
    await flushPromises()
    expect((wrapper.get('[data-testid="submit-batch"]').element as HTMLButtonElement).disabled).toBe(false)
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/jobs/job-validation'))).toBe(true)
    wrapper.unmount()
  })

  it('keeps validation locked for submitted, unknown, in-flight, mismatched, and unreadable jobs', async () => {
    const cases = [
      { name: 'prediction job', job: { resource_ref: { type: 'batch_prediction', id: 'batch-record-1' }, status: 'SUCCEEDED' } },
      { name: 'unknown resource', job: { resource_ref: { type: 'unknown', id: 'validation-1' }, status: 'SUCCEEDED' } },
      { name: 'in-flight validation', job: { resource_ref: { type: 'batch_validation', id: 'validation-1' }, status: 'RUNNING' } },
      { name: 'mismatched validation', job: { resource_ref: { type: 'batch_validation', id: 'validation-other' }, status: 'SUCCEEDED' } },
      { name: 'unreadable job', job: null },
    ] as const
    for (const item of cases) {
      const { capability, tool, draft, validation, job } = writableTlcFixtures()
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/prediction/types/tlc')) return response(capability)
        if (url.includes('/tools/batch/schema?')) return response(tool)
        if (url.endsWith('/batch-predictions/batch-record-1')) return response(draft)
        if (url.endsWith('/artifacts/artifact-input-1')) return response(artifact())
        if (url.endsWith('/batch-predictions/batch-record-1/validation')) return response(validation)
        if (url.includes('/validation/rows')) return response({ rows: [], has_more: false })
        if (url.includes('/batch-predictions/batch-record-1/rows')) return response({ rows: [], has_more: false })
        if (url.endsWith('/jobs/job-validation')) {
          if (!item.job) return new Response('', { status: 503 })
          return response({ ...job, ...item.job })
        }
        throw new Error(`Unexpected ${item.name} request: ${url}`)
      })
      const wrapper = mountedWorkspace(fetchMock, { initialBatchId: draft.id, canCreateBatch: true })
      await flushPromises()
      expect((wrapper.get('[aria-label="批量草稿与服务端校验"] button').element as HTMLButtonElement).disabled, item.name).toBe(true)
      expect(wrapper.find('[aria-label="批量提交确认"]').exists(), item.name).toBe(false)
      const exportButton = wrapper.findAll('button').find((button) => button.text() === '创建 CSV 导出')!
      const newBatchButton = wrapper.findAll('button').find((button) => button.text() === '新建批次')!
      expect((exportButton.element as HTMLButtonElement).disabled, item.name).toBe(item.name !== 'prediction job')
      expect((newBatchButton.element as HTMLButtonElement).disabled, item.name).toBe(item.name !== 'prediction job')
      const retryButton = wrapper.findAll('button').find((button) => button.text() === '创建失败行重试计划')
      if (retryButton) expect((retryButton.element as HTMLButtonElement).disabled, item.name).toBe(true)
      wrapper.unmount()
    }
  })

  it('allows cancellation of a freshly bound running job while keeping submission actions locked', async () => {
    const cases = [
      { type: 'batch_validation', id: 'validation-1', cancelable: true, visible: true },
      { type: 'batch_prediction', id: 'batch-record-1', cancelable: true, visible: true },
      { type: 'batch_prediction', id: 'another-batch', cancelable: true, visible: false },
      { type: 'unknown', id: 'validation-1', cancelable: true, visible: false },
      { type: 'batch_prediction', id: 'batch-record-1', cancelable: false, visible: false },
      { type: 'unreadable', id: 'batch-record-1', cancelable: true, visible: false },
    ]
    for (const item of cases) {
      const { capability, tool, draft, validation, job } = writableTlcFixtures()
      const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.endsWith('/prediction/types/tlc')) return response(capability)
        if (url.includes('/tools/batch/schema?')) return response(tool)
        if (url.endsWith('/batch-predictions/batch-record-1')) return response({ ...draft, execution_status: 'RUNNING' })
        if (url.endsWith('/artifacts/artifact-input-1')) return response(artifact())
        if (url.endsWith('/batch-predictions/batch-record-1/validation')) return response(validation)
        if (url.includes('/validation/rows') || url.includes('/batch-predictions/batch-record-1/rows')) return response({ rows: [], has_more: false })
        if (url.endsWith('/jobs/job-validation')) {
          if (item.type === 'unreadable') return new Response('', { status: 503 })
          return response({ ...job, status: 'RUNNING', cancelable: item.cancelable,
            resource_ref: { type: item.type, id: item.id } })
        }
        throw new Error(`Unexpected running cancellation request: ${url}`)
      })
      const wrapper = mountedWorkspace(fetchMock, { initialBatchId: draft.id, canCreateBatch: true, canCancelBatch: true })
      await flushPromises()
      expect(wrapper.findAll('button').some((button) => button.text() === '取消任务'), `${item.type}/${item.id}/${item.cancelable}`).toBe(item.visible)
      expect((wrapper.get('[aria-label="批量草稿与服务端校验"] button').element as HTMLButtonElement).disabled).toBe(true)
      expect(wrapper.find('[aria-label="批量提交确认"]').exists()).toBe(false)
      wrapper.unmount()
    }
  })

  it('requires a selected failed row before offering a retry plan for a bound terminal prediction', async () => {
    const { capability, tool, draft, validation, job } = writableTlcFixtures()
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/prediction/types/tlc')) return response(capability)
      if (url.includes('/tools/batch/schema?')) return response(tool)
      if (url.endsWith('/batch-predictions/batch-record-1')) return response({ ...draft, execution_status: 'FAILED' })
      if (url.endsWith('/artifacts/artifact-input-1')) return response(artifact())
      if (url.endsWith('/batch-predictions/batch-record-1/validation')) return response(validation)
      if (url.includes('/validation/rows')) return response({ rows: [], has_more: false })
      if (url.includes('/batch-predictions/batch-record-1/rows')) return response({ rows: [rowResult('FAILED')], has_more: false })
      if (url.endsWith('/jobs/job-validation')) return response({ ...job, status: 'FAILED', resource_ref: { type: 'batch_prediction', id: draft.id } })
      throw new Error(`Unexpected retry eligibility request: ${url}`)
    })
    const wrapper = mountedWorkspace(fetchMock, { initialBatchId: draft.id, canCreateBatch: true })
    await flushPromises()
    const retry = wrapper.findAll('button').find((button) => button.text() === '创建失败行重试计划')!
    expect((retry.element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('.retry-choice input').setValue(true)
    expect((retry.element as HTMLButtonElement).disabled).toBe(false)
    expect(wrapper.find('[aria-label="批量提交确认"]').exists()).toBe(false)
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/submit') || String(url).includes('/retry-failed'))).toBe(false)
    wrapper.unmount()
  })

  it('allows revalidation after a terminal failed validation even when draft revision has since advanced', async () => {
    const { capability, tool, draft, job } = writableTlcFixtures()
    const invalidDraft = { ...draft, revision: 2, etag: '"batch-r2"', draft_status: 'INVALID' }
    const staleValidation = { ...writableTlcFixtures().validation, status: 'INVALID', draft_revision: 1 }
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/prediction/types/tlc')) return response(capability)
      if (url.includes('/tools/batch/schema?')) return response(tool)
      if (url.endsWith('/batch-predictions/batch-record-1')) return response(invalidDraft)
      if (url.endsWith('/artifacts/artifact-input-1')) return response(artifact())
      if (url.endsWith('/batch-predictions/batch-record-1/validation')) return response(staleValidation)
      if (url.includes('/validation/rows')) return response({ rows: [], has_more: false })
      if (url.includes('/batch-predictions/batch-record-1/rows')) return response({ rows: [], has_more: false })
      if (url.endsWith('/jobs/job-validation')) return response({ ...job, status: 'FAILED' })
      throw new Error(`Unexpected failed validation recovery request: ${url}`)
    })
    const wrapper = mountedWorkspace(fetchMock, { initialBatchId: invalidDraft.id, canCreateBatch: true })
    await flushPromises()
    expect((wrapper.get('[aria-label="批量草稿与服务端校验"] button').element as HTMLButtonElement).disabled).toBe(false)
    expect(wrapper.find('[aria-label="批量提交确认"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
