import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TlcBatchWorkspace from '../candidates/tlc-batch-revision20261008-01/src/modules/prediction/forms/tlc/TlcBatchWorkspace.vue'
import type { TlcBatchToolCapability, TlcBatchTypeCapability } from '../candidates/tlc-batch-revision20261008-01/src/modules/prediction/forms/tlc/tlc-batch-client'

vi.mock('@/components/JobStatus.vue', () => ({ default: { props: ['jobId'], template: '<div data-testid="job-status">{{ jobId }}</div>' } }))

function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, request_id: 'candidate-test', warnings: [] }), { status, headers: { 'content-type': 'application/json' } })
}

function typeCapability(modelStatus: 'READY' | 'UNAVAILABLE' = 'READY', templateVersion = 'tlc-batch-2'): TlcBatchTypeCapability {
  return {
    id: 'tlc', display_name: 'TLC', health: { status: 'READY', checked_at: '2026-10-08T00:00:00Z', reason: null },
    limits: { max_rows: 100, max_candidates: 10, max_evaluations: 100 },
    models: [{ id: 'model-server-id', version: 'v1.1', input_schema_version: '1.1', output_schema_version: '1.1', scope: 'TLC model', task_type: 'tlc', health: { status: modelStatus, checked_at: '2026-10-08T00:00:00Z', reason: modelStatus === 'READY' ? null : '模型暂不可用' } }],
    tools: [toolCapability(templateVersion)],
  }
}

function toolCapability(templateVersion = 'tlc-batch-2'): TlcBatchToolCapability {
  return {
    id: 'batch', chromatography_type: 'tlc', schema_version: '1.1', template_version: templateVersion,
    health: { status: 'READY', checked_at: '2026-10-08T00:00:00Z', reason: null },
    limits: { max_runtime_seconds: 3600, max_evaluations: 100 },
    input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} },
    model_constraints: {
      template_versions: ['tlc-batch-1', 'tlc-batch-2'],
      compatible_models: [{ model_id: 'model-server-id', model_version: 'v1.1', input_schema_version: '1.1', template_versions: ['tlc-batch-1', 'tlc-batch-2'], health: { status: 'READY', checked_at: '2026-10-08T00:00:00Z', reason: null } }],
    },
    structure_count: { min: 1, max: 1, semantics: 'batch' }, supported_visualizations: [], units: {},
    templates: [{ id: 'tlc_batch', chromatography_type: 'tlc', tool: 'batch', version: templateVersion,
      row_schema: 'TlcBatchRowV2', fields: ['smiles', 'solvent_1', 'fraction_1', 'solvent_2', 'fraction_2'],
      limits: { max_runtime_seconds: 3600, max_evaluations: 100 }, health: { status: 'READY', checked_at: '2026-10-08T00:00:00Z', reason: null },
      tlc_options: { solvent_options: [{ id: 'hexane' }, { id: 'ethyl_acetate' }], allowed_systems: [{ id: 'hexane-ethyl_acetate', solvent_ids: ['hexane', 'ethyl_acetate'] }] } }],
  }
}

function artifactView(status: 'VALIDATING' | 'AVAILABLE' = 'AVAILABLE') {
  return {
    id: 'artifact-server-issued', project_id: 'project-from-context', kind: 'batch_input', status,
    scan_status: status === 'AVAILABLE' ? 'PASSED' : 'PENDING', file_name: 'input.csv', media_type: 'text/csv', size_bytes: 42,
    owner_id: 'actor', created_at: '2026-10-08T00:00:00Z', updated_at: '2026-10-08T00:00:01Z', download_allowed: false, sha256: null,
  }
}

function recoveryDraft(overrides: Record<string, unknown> = {}) {
  return {
    id: 'batch-server', project_id: 'project-from-context', chromatography_type: 'tlc', tool: 'batch', config_hash: 'b'.repeat(64),
    draft_status: 'READY', execution_status: null, job_id: null, counts: { total: 1, queued: 0, running: 0, succeeded: 0, failed: 0, skipped: 0, canceled: 0 }, progress: 0,
    revision: 1, etag: '"batch-etag-r1"', name: 'TLC batch', schema_version: '1.1',
    model_selection: { mode: 'exact', model_id: 'model-server-id', model_version: 'v1.1' }, input_artifact_id: 'artifact-server-issued',
    template_version: 'tlc-batch-2', field_mapping: { smiles: 'smiles', solvent_1: 'solvent_1', fraction_1: 'fraction_1', solvent_2: 'solvent_2', fraction_2: 'fraction_2' },
    common_input: {}, invalid_row_policy: 'exclude_invalid', resource_budget: { max_evaluations: 100, max_runtime_seconds: 3600 }, validation_id: 'validation-1',
    ...overrides,
  }
}

function recoveryValidation(overrides: Record<string, unknown> = {}) {
  return {
    id: 'validation-1', draft_revision: 1, config_hash: 'b'.repeat(64), status: 'READY', template_version: 'tlc-batch-2',
    model_snapshot: { id: 'model-server-id', version: 'v1.1', input_schema_version: '1.1', output_schema_version: '1.1', artifact_sha256: 'e'.repeat(64), business_scope_id: 'scope-tlc' },
    total_rows: 1, valid_rows: 1, invalid_rows: 0, excluded_rows: 0, total_evaluations: 1,
    resource_estimate: { max_evaluations: 100, max_runtime_seconds: 3600 }, resolved_input_hash: 'd'.repeat(64), ...overrides,
  }
}

function recoverySnapshot() {
  return { chromatography_type: 'tlc', template_version: 'tlc-batch-2', project_id: 'project-from-context', draft_revision: 1,
    validation_id: 'validation-1', structure_ref: 'structure-server', normalized_smiles: 'CCO',
    solvent_conditions: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.7 }, { solvent_id: 'ethyl_acetate', fraction: 0.3 }] } }
}

beforeEach(() => { setActivePinia(createPinia()); sessionStorage.clear() })
afterEach(() => vi.restoreAllMocks())

describe('TLC batch candidate workspace', () => {
  it('accepts an exact model that supports a subset of the declared templates', async () => {
    const batch = toolCapability()
    batch.model_constraints.compatible_models[0]!.template_versions = ['tlc-batch-2']
    const capability = typeCapability()
    capability.tools = [batch]
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/prediction/types/tlc')) return envelope(capability)
      if (url.includes('/tools/batch/schema?')) return envelope(batch)
      throw new Error(`Unexpected template subset request: ${url}`)
    }))
    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true } })
    await flushPromises()
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    expect((wrapper.get('[data-testid="download-tlc-template"]').element as HTMLButtonElement).disabled).toBe(false)
    wrapper.unmount()
  })

  it('downloads the server v2 template and uploads a selected CSV through the artifact service', async () => {
    const requests: Array<{ url: string; method: string; body?: BodyInit | null }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({ url, method, body: init?.body })
      if (url.endsWith('/prediction/types/tlc')) return envelope(typeCapability())
      if (url.includes('/tools/batch/schema?')) return envelope(toolCapability())
      if (url.includes('/batch-template?')) return new Response('smiles,solvent_1,fraction_1,solvent_2,fraction_2', { status: 200, headers: { 'content-type': 'text/csv', 'X-Template-Version': 'tlc-batch-2', 'X-Template-SHA256': 'a'.repeat(64) } })
      if (url.endsWith('/artifacts/uploads') && method === 'POST') return envelope({ id: 'upload-1', artifact_id: 'artifact-server-issued', content_url: '/content', max_size_bytes: 10000, allowed_media_types: ['text/csv'], received_bytes: 0, status: 'CREATED', expires_at: '2026-10-08T01:00:00Z' }, 201)
      if (url.endsWith('/artifacts/uploads/upload-1/content')) return envelope({ upload_id: 'upload-1', artifact_id: 'artifact-server-issued', received_bytes: 42, sha256: 'b'.repeat(64), received_at: '2026-10-08T00:00:00Z' })
      if (url.endsWith('/artifacts/uploads/upload-1/complete')) return envelope({ artifact_id: 'artifact-server-issued', id: 'artifact-server-issued', job_id: 'job-validation', resource_ref: { type: 'artifact', id: 'artifact-server-issued' }, status: 'VALIDATING' }, 202)
      if (url.endsWith('/artifacts/artifact-server-issued')) return envelope(artifactView())
      throw new Error(`Unexpected test request: ${method} ${url}`)
    }))
    const NativeURL = URL
    vi.stubGlobal('URL', class extends NativeURL {
      static createObjectURL = vi.fn(() => 'blob:template')
      static revokeObjectURL = vi.fn()
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)

    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true } })
    await flushPromises()
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    expect((wrapper.get('[data-testid="download-tlc-template"]').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.get('[data-testid="download-tlc-template"]').trigger('click')
    await flushPromises()

    const fileInput = wrapper.get('[data-testid="batch-file-input"]')
    const file = new File(['smiles,solvent_1,fraction_1,solvent_2,fraction_2\nC,water,0.5,ethanol,0.5'], 'input.csv', { type: 'text/csv' })
    Object.defineProperty(fileInput.element, 'files', { configurable: true, value: [file] })
    await fileInput.trigger('change')
    await flushPromises()
    await wrapper.get('[data-testid="upload-batch-file"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="artifact-ready"]').text()).toContain('文件可用')
    expect(wrapper.get('[data-testid="job-status"]').text()).toBe('job-validation')
    expect(wrapper.get('[data-testid="draft-gate"]').text()).toContain('逐行预览和提交尚未就绪')
    const createRequest = requests.find((request) => request.url.endsWith('/artifacts/uploads') && request.method === 'POST')!
    expect(JSON.parse(createRequest.body as string)).toEqual({ project_id: 'project-from-context', kind: 'batch_input', file_name: 'input.csv', media_type: 'text/csv', size_bytes: file.size })
    expect(requests.some((request) => request.url.includes('/batch-predictions/'))).toBe(false)
    wrapper.unmount()
  })

  it('blocks old templates and does not fetch capability without current access permission', async () => {
    let advertisesBatch = true
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/tools/batch/schema?')) return envelope(toolCapability('tlc-batch-1'))
      const capability = typeCapability('READY', 'tlc-batch-1')
      return envelope(advertisesBatch ? capability : { ...capability, tools: [] })
    })
    vi.stubGlobal('fetch', fetch)
    const denied = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: false, canUploadBatch: false } })
    await flushPromises()
    expect(fetch).not.toHaveBeenCalled()
    denied.unmount()

    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true } })
    await flushPromises()
    expect((wrapper.findAll('[data-testid="batch-model-select"] option').find((option) => option.text().includes('TLC model'))!.element as HTMLOptionElement).disabled).toBe(false)
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    expect(fetch).toHaveBeenCalledTimes(2)
    expect(wrapper.get('[data-testid="batch-tool-ready"]').text()).toContain('tlc-batch-1')
    expect(wrapper.findAll('[data-testid="legacy-template-blocked"]')).toHaveLength(1)
    expect((wrapper.get('[data-testid="download-tlc-template"]').element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('[data-testid="upload-batch-file"]').element as HTMLButtonElement).disabled).toBe(true)
    wrapper.unmount()

    advertisesBatch = false
    fetch.mockClear()
    const missingTool = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true } })
    await flushPromises()
    await missingTool.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    expect(missingTool.findAll('[data-testid="batch-tool-ready"]')).toHaveLength(0)
    expect((missingTool.get('[data-testid="download-tlc-template"]').element as HTMLButtonElement).disabled).toBe(true)
    expect((missingTool.get('[data-testid="upload-batch-file"]').element as HTMLButtonElement).disabled).toBe(true)
    missingTool.unmount()
  })

  it('drops delayed upload responses after project context changes and clears the busy state', async () => {
    let resolveCreateUpload!: (response: Response) => void
    let delayed = true
    const requests: Array<{ url: string; method: string }> = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url.endsWith('/prediction/types/tlc')) return Promise.resolve(envelope(typeCapability()))
      if (url.includes('/tools/batch/schema?')) return Promise.resolve(envelope(toolCapability()))
      if (url.includes('/batch-template?')) return Promise.resolve(new Response('smiles,solvent_1,fraction_1,solvent_2,fraction_2', { status: 200, headers: { 'content-type': 'text/csv', 'X-Template-Version': 'tlc-batch-2', 'X-Template-SHA256': 'a'.repeat(64) } }))
      if (url.endsWith('/artifacts/uploads') && method === 'POST' && delayed) return new Promise<Response>((resolve) => { resolveCreateUpload = resolve })
      if (url.endsWith('/artifacts/uploads') && method === 'POST') return Promise.resolve(envelope({ id: 'upload-2', artifact_id: 'artifact-2', content_url: '/content', max_size_bytes: 10000, allowed_media_types: ['text/csv'], received_bytes: 0, status: 'CREATED', expires_at: '2026-10-08T01:00:00Z' }, 201))
      return Promise.reject(new Error(`Unexpected stale request: ${method} ${url}`))
    }))
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true } })
    await flushPromises()
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    await wrapper.get('[data-testid="download-tlc-template"]').trigger('click')
    await flushPromises()
    const oldFileInput = wrapper.get('[data-testid="batch-file-input"]')
    Object.defineProperty(oldFileInput.element, 'files', { configurable: true, value: [new File(['row'], 'old.csv', { type: 'text/csv' })] })
    await oldFileInput.trigger('change')
    await wrapper.get('[data-testid="upload-batch-file"]').trigger('click')
    await flushPromises()
    expect(requests.some((request) => request.url.endsWith('/artifacts/uploads') && request.method === 'POST')).toBe(true)

    await wrapper.setProps({ projectId: 'project-after-context-change' })
    await flushPromises()
    delayed = false
    resolveCreateUpload(envelope({ id: 'stale-upload', artifact_id: 'stale-artifact', content_url: '/content', max_size_bytes: 10000, allowed_media_types: ['text/csv'], received_bytes: 0, status: 'CREATED', expires_at: '2026-10-08T01:00:00Z' }, 201))
    await flushPromises()

    expect(requests.some((request) => request.url.endsWith('/artifacts/uploads/stale-upload/content'))).toBe(false)
    expect(requests.some((request) => request.url.endsWith('/artifacts/uploads/stale-upload/complete'))).toBe(false)
    expect(wrapper.findAll('[aria-label="服务端制品状态"]')).toHaveLength(0)
    expect((wrapper.get('[data-testid="batch-model-select"]').element as HTMLSelectElement).value).toBe('')
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    await wrapper.get('[data-testid="download-tlc-template"]').trigger('click')
    await flushPromises()
    const newFileInput = wrapper.get('[data-testid="batch-file-input"]')
    Object.defineProperty(newFileInput.element, 'files', { configurable: true, value: [new File(['new row'], 'new.csv', { type: 'text/csv' })] })
    await newFileInput.trigger('change')
    await flushPromises()
    expect((wrapper.get('[data-testid="batch-file-input"]').element as HTMLInputElement).disabled).toBe(false)
    expect((wrapper.get('[data-testid="upload-batch-file"]').element as HTMLButtonElement).disabled).toBe(false)
    wrapper.unmount()
  })
})

describe('TLC batch template busy guard', () => {
  it('blocks template download during upload and re-enables it after the artifact check', async () => {
    let resolveCreateUpload!: (response: Response) => void
    const requests: string[] = []
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      requests.push(`${init?.method ?? 'GET'} ${url}`)
      if (url.endsWith('/prediction/types/tlc')) return Promise.resolve(envelope(typeCapability()))
      if (url.includes('/tools/batch/schema?')) return Promise.resolve(envelope(toolCapability()))
      if (url.includes('/batch-template?')) return Promise.resolve(new Response('smiles,solvent_1,fraction_1,solvent_2,fraction_2', { status: 200, headers: { 'content-type': 'text/csv', 'X-Template-Version': 'tlc-batch-2', 'X-Template-SHA256': 'a'.repeat(64) } }))
      if (url.endsWith('/artifacts/uploads') && init?.method === 'POST') return new Promise<Response>((resolve) => { resolveCreateUpload = resolve })
      if (url.endsWith('/artifacts/uploads/upload-pending/content')) return Promise.resolve(envelope({ upload_id: 'upload-pending', artifact_id: 'artifact-pending', received_bytes: 5, sha256: 'b'.repeat(64), received_at: '2026-10-08T00:00:00Z' }))
      if (url.endsWith('/artifacts/uploads/upload-pending/complete')) return Promise.resolve(envelope({ artifact_id: 'artifact-pending', id: 'artifact-pending', job_id: 'job-pending', resource_ref: { type: 'artifact', id: 'artifact-pending' }, status: 'VALIDATING' }, 202))
      if (url.endsWith('/artifacts/artifact-pending')) return Promise.resolve(envelope({ ...artifactView(), id: 'artifact-pending', status: 'AVAILABLE', scan_status: 'PASSED' }))
      return Promise.reject(new Error(`Unexpected test request: ${init?.method ?? 'GET'} ${url}`))
    }))
    const NativeURL = URL
    vi.stubGlobal('URL', class extends NativeURL {
      static createObjectURL = vi.fn(() => 'blob:template')
      static revokeObjectURL = vi.fn()
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)

    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true } })
    await flushPromises()
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    await wrapper.get('[data-testid="download-tlc-template"]').trigger('click')
    await flushPromises()
    const input = wrapper.get('[data-testid="batch-file-input"]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['row'], 'input.csv', { type: 'text/csv' })] })
    await input.trigger('change')
    await wrapper.get('[data-testid="upload-batch-file"]').trigger('click')
    await flushPromises()

    const downloadButton = wrapper.get('[data-testid="download-tlc-template"]')
    expect((downloadButton.element as HTMLButtonElement).disabled).toBe(true)
    await downloadButton.trigger('click')
    expect(requests.filter((request) => request.includes('/batch-template?'))).toHaveLength(1)

    resolveCreateUpload(envelope({ id: 'upload-pending', artifact_id: 'artifact-pending', content_url: '/content', max_size_bytes: 10000, allowed_media_types: ['text/csv'], received_bytes: 0, status: 'CREATED', expires_at: '2026-10-08T01:00:00Z' }, 201))
    await flushPromises()
    expect(wrapper.findAll('[data-testid="artifact-ready"]')).toHaveLength(1)
    expect((wrapper.get('[data-testid="download-tlc-template"]').element as HTMLButtonElement).disabled).toBe(false)
    wrapper.unmount()
  })
})

describe('TLC batch candidate lifecycle', () => {
  it('binds v2 preview, distinct validation and confirmation hashes, submit, skipped rows and results', async () => {
    const requests: Array<{ url: string; method: string; headers: Headers; body?: BodyInit | null }> = []
    const configHash = 'b'.repeat(64)
    const targetHash = 'c'.repeat(64)
    const draft = (status: 'DRAFT' | 'READY' = 'DRAFT', jobId: string | null = null) => ({
      id: 'batch-server', project_id: 'project-from-context', chromatography_type: 'tlc', tool: 'batch', config_hash: configHash,
      draft_status: status, execution_status: jobId ? 'QUEUED' : null, job_id: jobId, counts: { total: 2, queued: 1, running: 0, succeeded: 0, failed: 0, skipped: 1, canceled: 0 }, progress: 0,
      revision: 1, etag: '"batch-etag-r1"', name: 'TLC batch', schema_version: '1.1',
      model_selection: { mode: 'exact', model_id: 'model-server-id', model_version: 'v1.1' }, input_artifact_id: 'artifact-server-issued',
      template_version: 'tlc-batch-2', field_mapping: { smiles: 'smiles', solvent_1: 'solvent_1', fraction_1: 'fraction_1', solvent_2: 'solvent_2', fraction_2: 'fraction_2' },
      common_input: {}, invalid_row_policy: 'exclude_invalid', resource_budget: { max_evaluations: 100, max_runtime_seconds: 3600 }, validation_id: status === 'READY' ? 'validation-1' : null,
    })
    const validSnapshot = { chromatography_type: 'tlc', template_version: 'tlc-batch-2', project_id: 'project-from-context', draft_revision: 1, validation_id: 'validation-1', structure_ref: 'structure-server', normalized_smiles: 'CCO', solvent_conditions: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.7 }, { solvent_id: 'ethyl_acetate', fraction: 0.3 }] } }
    const artifactHash = 'e'.repeat(64)
    const validation = { id: 'validation-1', draft_revision: 1, config_hash: configHash, status: 'READY', template_version: 'tlc-batch-2', model_snapshot: { id: 'model-server-id', version: 'v1.1', input_schema_version: '1.1', output_schema_version: '1.1', artifact_sha256: artifactHash, business_scope_id: 'scope-tlc' }, total_rows: 2, valid_rows: 1, invalid_rows: 1, excluded_rows: 1, total_evaluations: 1, resource_estimate: { max_evaluations: 100, max_runtime_seconds: 3600 }, resolved_input_hash: 'd'.repeat(64) }
    let submitted = false
    let canceled = false
    let resolveDraftCreate: ((response: Response) => void) | null = null
    let resolveSubmit: ((response: Response) => void) | null = null
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'
      requests.push({ url, method, headers: new Headers(init?.headers), body: init?.body })
      if (url.endsWith('/prediction/types/tlc')) return envelope(typeCapability())
      if (url.includes('/tools/batch/schema?')) return envelope(toolCapability())
      if (url.includes('/batch-template?')) return new Response('smiles,solvent_1,fraction_1,solvent_2,fraction_2', { status: 200, headers: { 'content-type': 'text/csv', 'X-Template-Version': 'tlc-batch-2', 'X-Template-SHA256': 'a'.repeat(64) } })
      if (url.endsWith('/artifacts/uploads') && method === 'POST') return envelope({ id: 'upload-1', artifact_id: 'artifact-server-issued', content_url: '/content', max_size_bytes: 10000, allowed_media_types: ['text/csv'], received_bytes: 0, status: 'CREATED', expires_at: '2026-10-08T01:00:00Z' }, 201)
      if (url.endsWith('/artifacts/uploads/upload-1/content')) return envelope({ upload_id: 'upload-1', artifact_id: 'artifact-server-issued', received_bytes: 42, sha256: 'a'.repeat(64), received_at: '2026-10-08T00:00:00Z' })
      if (url.endsWith('/artifacts/uploads/upload-1/complete')) return envelope({ artifact_id: 'artifact-server-issued', id: 'artifact-server-issued', job_id: 'artifact-job', resource_ref: { type: 'artifact', id: 'artifact-server-issued' }, status: 'VALIDATING' }, 202)
      if (url.endsWith('/artifacts/artifact-server-issued')) return envelope(artifactView())
      if (url.endsWith('/batch-predictions/drafts') && method === 'POST') return await new Promise<Response>((resolve) => { resolveDraftCreate = resolve })
      if (url.endsWith('/batch-predictions/batch-server/validate')) return envelope({ id: 'validation-1', job_id: 'validation-job', resource_ref: { type: 'batch_validation', id: 'validation-1' }, status: 'QUEUED' }, 202)
      if (url.endsWith('/batch-predictions/batch-server/validation')) return envelope(validation)
      if (url.includes('/batch-predictions/batch-server/validation/rows')) return envelope({ rows: [
        { row_id: 'row-valid', source_row_number: 2, status: 'VALID', normalized_input_hash: '1'.repeat(64), resolved_input_hash: '2'.repeat(64), resolved_input_snapshot: validSnapshot, warnings: [] },
        { row_id: 'row-invalid', source_row_number: 3, status: 'INVALID', normalized_input_hash: '3'.repeat(64), resolved_input_hash: null, resolved_input_snapshot: null, field_errors: [{ stage: 'VALIDATION', message: 'SMILES 未通过验证' }], warnings: [] },
      ], has_more: false })
      if (url.endsWith('/confirmations/previews')) return envelope({
        resource_ref: { type: 'batch_prediction', id: 'batch-server' }, action: 'batch.submit', revision: 1, config_hash: targetHash,
        etag: '"batch-etag-r1"', policy_version: '1', can_confirm: true, blockers: [], dependencies: [], impact: { row_count: 1 },
        summary: [{ label: '有效行', value: 1 }, { label: '预计 evaluations', value: 1 }],
      })
      if (url.endsWith('/confirmations') && method === 'POST') return envelope({
        id: 'confirmation-1', project_id: 'project-from-context', actor_id: 'actor', resource_ref: { type: 'batch_prediction', id: 'batch-server' },
        action: 'batch.submit', revision: 1, config_hash: targetHash, policy_version: '1', summary_snapshot: [], status: 'CONFIRMED',
        created_at: '2026-10-08T00:00:00Z', expires_at: '2026-10-08T01:00:00Z', consumed_at: null, consumed_result_ref: null,
      }, 201)
      if (url.endsWith('/batch-predictions/batch-server/submit')) {
        return await new Promise<Response>((resolve) => { resolveSubmit = (response) => { submitted = true; resolve(response) } })
      }
      if (url.endsWith('/batch-predictions/batch-server/cancel') && method === 'POST') { canceled = true; return envelope({ ...draft('READY', 'prediction-job'), execution_status: 'CANCELED' }) }
      if (url.includes('/batch-predictions/batch-server/rows')) return envelope({ rows: [
        { row_id: 'row-valid', source_row_number: 2, input_snapshot: validSnapshot, result: { task_type: 'tlc', schema_version: '1.1', model_id: 'model-server-id', model_version: 'v1.1', artifact_sha256: artifactHash, ensemble_weights: { model: 1 }, mobile_phase_original: { hexane: 0.7, ethyl_acetate: 0.3 }, mobile_phase_normalized: { hexane: 0.7, ethyl_acetate: 0.3 }, rows: [{ rf: 0.47 }], applicability: { supported_scope: 'scope-tlc', warnings: [{ code: 'OUT_OF_SCOPE', message: '超出模型范围' }] } }, error: null, warnings: [], attempt: 1, status: 'SUCCEEDED' },
        { row_id: 'row-invalid', source_row_number: 3, input_snapshot: {}, result: null, error: { stage: 'VALIDATION', message: 'SMILES 未通过验证' }, warnings: [], attempt: 1, status: 'SKIPPED' },
      ], has_more: false })
      if (url.endsWith('/batch-predictions/batch-server')) return envelope(canceled ? { ...draft('READY', 'prediction-job'), execution_status: 'CANCELED' } : draft('READY', submitted ? 'prediction-job' : null), 200)
      throw new Error(`Unexpected lifecycle test request: ${method} ${url}`)
    }))
    const NativeURL = URL
    vi.stubGlobal('URL', class extends NativeURL { static createObjectURL = vi.fn(() => 'blob:template'); static revokeObjectURL = vi.fn() })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true, canCreateBatch: true, canCancelBatch: true } })
    await flushPromises()
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    await wrapper.get('[data-testid="download-tlc-template"]').trigger('click')
    await flushPromises()
    const input = wrapper.get('[data-testid="batch-file-input"]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [new File(['smiles,solvent_1,fraction_1,solvent_2,fraction_2\nCCO,hexane,0.7,ethyl_acetate,0.3'], 'input.csv', { type: 'text/csv' })] })
    await input.trigger('change'); await wrapper.get('[data-testid="upload-batch-file"]').trigger('click'); await flushPromises()
    await wrapper.get('[data-testid="invalid-row-policy"]').setValue('exclude_invalid')
    await wrapper.get('[data-testid="create-batch-draft"]').trigger('click'); await flushPromises()
    expect(resolveDraftCreate).toBeTruthy()
    expect((wrapper.get('[data-testid="download-tlc-template"]').element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('[data-testid="refresh-artifact"]').element as HTMLButtonElement).disabled).toBe(true)
    resolveDraftCreate!(envelope({ ...draft(), field_mapping: { fraction_2: 'fraction_2', solvent_2: 'solvent_2', fraction_1: 'fraction_1', solvent_1: 'solvent_1', smiles: 'smiles' } }, 201))
    await flushPromises()
    expect(wrapper.findAll('[data-testid="create-batch-draft"]')).toHaveLength(1)
    expect((wrapper.get('[data-testid="create-batch-draft"]').element as HTMLButtonElement).disabled).toBe(true)
    const createRequest = requests.find((request) => request.url.endsWith('/batch-predictions/drafts'))!
    expect(JSON.parse(createRequest.body as string)).toMatchObject({ chromatography_type: 'tlc', tool: 'batch', project_id: 'project-from-context', template_version: 'tlc-batch-2', invalid_row_policy: 'exclude_invalid' })
    expect(JSON.parse(createRequest.body as string).field_mapping).toEqual({ smiles: 'smiles', solvent_1: 'solvent_1', fraction_1: 'fraction_1', solvent_2: 'solvent_2', fraction_2: 'fraction_2' })

    await wrapper.get('[data-testid="validate-batch-draft"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-testid="typed-preview"]').text()).toContain('CCO')
    expect(wrapper.get('[data-testid="typed-preview"]').text()).toContain('hexane 70%')
    expect(wrapper.get('[data-testid="typed-preview"]').text()).toContain('ethyl_acetate 30%')
    expect(wrapper.get('[data-testid="typed-preview"]').text()).toContain('无效，未执行预测')
    expect(wrapper.get('[data-testid="validation-status-message"]').text()).toContain('跳过')
    await wrapper.get('[data-testid="load-submit-preview"]').trigger('click'); await flushPromises()
    expect(wrapper.findAll('[data-testid="create-submit-confirmation"]')).toHaveLength(1)
    await wrapper.get('[data-testid="create-submit-confirmation"]').trigger('click'); await flushPromises()
    expect((wrapper.get('[data-testid="submit-batch"]').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.get('[data-testid="submit-batch"]').trigger('click'); await flushPromises()
    expect(resolveSubmit).toBeTruthy()
    expect((wrapper.get('[data-testid="download-tlc-template"]').element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('[data-testid="refresh-artifact"]').element as HTMLButtonElement).disabled).toBe(true)
    resolveSubmit!(envelope({ id: 'batch-server', job_id: 'prediction-job', attempt: 1, execution_status: 'QUEUED', resource_ref: { type: 'batch_prediction', id: 'batch-server' } }, 202))
    await flushPromises()
    expect(submitted).toBe(true)
    expect(wrapper.findAll('[data-testid="cancel-batch"]')).toHaveLength(1)
    await wrapper.get('[data-testid="refresh-batch-results"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-testid="batch-result-rows"]').text()).toContain('Rf：0.47')
    expect(wrapper.get('[data-testid="batch-result-rows"]').text()).toContain('已跳过，未执行预测')
    expect(wrapper.get('[data-testid="batch-result-rows"]').text()).toContain('超出模型范围')
    expect(wrapper.get('[data-testid="batch-result-rows"]').text()).not.toContain('{"predicted_rf"')
    await wrapper.get('[data-testid="cancel-batch"]').trigger('click'); await flushPromises()
    expect(canceled).toBe(true)
    expect(requests.find((request) => request.url.endsWith('/batch-predictions/batch-server/cancel'))?.headers.get('if-match')).toBe('"batch-etag-r1"')
    expect(wrapper.findAll('[data-testid="cancel-batch"]')).toHaveLength(0)
    const postsBeforeReset = requests.filter((request) => request.method === 'POST').length
    await wrapper.get('[data-testid="new-batch"]').trigger('click'); await flushPromises()
    expect(wrapper.findAll('.draft-state')).toHaveLength(0)
    expect(sessionStorage.getItem('tlc-batch-candidate.active.v1')).toBeNull()
    expect(requests.filter((request) => request.method === 'POST')).toHaveLength(postsBeforeReset)
    expect(requests.find((request) => request.url.endsWith('/confirmations/previews'))).toBeTruthy()
    expect(requests.find((request) => request.url.endsWith('/confirmations') && request.method === 'POST')).toBeTruthy()
    expect(JSON.parse(requests.find((request) => request.url.endsWith('/batch-predictions/batch-server/submit'))!.body as string)).toEqual({ confirmation_id: 'confirmation-1' })
    wrapper.unmount()
  })
})

describe('TLC batch candidate recovery and validation binding', () => {
  const rejectedCases = [
    { name: 'validation still running', draft: { draft_status: 'VALIDATING' }, validation: { status: 'RUNNING' }, invalid: false },
    { name: 'block policy with invalid rows', draft: { draft_status: 'INVALID', invalid_row_policy: 'block' }, validation: { status: 'INVALID', valid_rows: 0, invalid_rows: 1 }, invalid: true },
    { name: 'config hash drift', draft: {}, validation: { config_hash: 'f'.repeat(64) }, invalid: false },
    { name: 'revision drift', draft: {}, validation: { draft_revision: 2 }, invalid: false },
    { name: 'model snapshot drift', draft: {}, validation: { model_snapshot: { id: 'different-model', version: 'v1.1', input_schema_version: '1.1', output_schema_version: '1.1', artifact_sha256: 'e'.repeat(64), business_scope_id: 'scope-tlc' } }, invalid: false },
  ]

  it.each(rejectedCases)('does not expose confirmation or submit for $name', async (scenario) => {
    const requests: Array<{ url: string; method: string }> = []
    const currentDraft = recoveryDraft(scenario.draft)
    const currentValidation = recoveryValidation({ ...scenario.validation, total_rows: 1 })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/prediction/types/tlc')) return envelope(typeCapability())
      if (url.includes('/tools/batch/schema?')) return envelope(toolCapability())
      if (url.endsWith('/batch-predictions/batch-server') && method === 'GET') return envelope(currentDraft)
      if (url.endsWith('/artifacts/artifact-server-issued')) return envelope(artifactView())
      if (url.endsWith('/batch-predictions/batch-server/validation')) return envelope(currentValidation)
      if (url.includes('/batch-predictions/batch-server/validation/rows')) return envelope({ rows: [scenario.invalid
        ? { row_id: 'row-invalid', source_row_number: 2, status: 'INVALID', normalized_input_hash: '1'.repeat(64), resolved_input_hash: null, resolved_input_snapshot: null, field_errors: [{ stage: 'VALIDATION', message: 'SMILES 无效' }], warnings: [] }
        : { row_id: 'row-valid', source_row_number: 2, status: 'VALID', normalized_input_hash: '1'.repeat(64), resolved_input_hash: '2'.repeat(64), resolved_input_snapshot: recoverySnapshot(), warnings: [] }], has_more: false })
      throw new Error(`Unexpected validation recovery request: ${method} ${url}`)
    }))
    sessionStorage.setItem('tlc-batch-candidate.active.v1', JSON.stringify({ project_id: 'project-from-context', batch_id: 'batch-server' }))
    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true, canCreateBatch: true } })
    await flushPromises()
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    await wrapper.get('[data-testid="restore-batch-draft"]').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-testid="load-submit-preview"]')).toHaveLength(0)
    expect(wrapper.findAll('[data-testid="submit-batch"]')).toHaveLength(0)
    expect(requests.some((request) => request.url.includes('/confirmations') || request.url.endsWith('/submit'))).toBe(false)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    if (scenario.name === 'validation still running') expect((wrapper.get('[data-testid="new-batch"]').element as HTMLButtonElement).disabled).toBe(true)
    wrapper.unmount()
  })

  it('restores matching running work with GET only and clears pointers for mismatched bindings', async () => {
    const requests: Array<{ url: string; method: string }> = []
    let draftResponse = recoveryDraft({ job_id: 'prediction-job', execution_status: 'RUNNING' })
    let artifactResponse = artifactView()
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/prediction/types/tlc')) return envelope(typeCapability())
      if (url.includes('/tools/batch/schema?')) return envelope(toolCapability())
      if (url.endsWith('/batch-predictions/batch-server') && method === 'GET') return envelope(draftResponse)
      if (url.endsWith('/artifacts/artifact-server-issued')) return envelope(artifactResponse)
      if (url.endsWith('/batch-predictions/batch-server/validation')) return envelope(recoveryValidation())
      if (url.includes('/batch-predictions/batch-server/validation/rows')) return envelope({ rows: [{ row_id: 'row-valid', source_row_number: 2, status: 'VALID', normalized_input_hash: '1'.repeat(64), resolved_input_hash: '2'.repeat(64), resolved_input_snapshot: recoverySnapshot(), warnings: [] }], has_more: false })
      if (url.includes('/batch-predictions/batch-server/rows')) return envelope({ rows: [], has_more: false })
      throw new Error(`Unexpected restore request: ${method} ${url}`)
    }))
    sessionStorage.setItem('tlc-batch-candidate.active.v1', JSON.stringify({ project_id: 'project-from-context', batch_id: 'batch-server' }))
    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true } })
    await flushPromises()
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    await wrapper.get('[data-testid="restore-batch-draft"]').trigger('click')
    await flushPromises()
    expect(requests.some((request) => request.url.includes('/rows?'))).toBe(true)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    expect(wrapper.get('.results-panel').text()).toContain('尚未读取行结果')

    for (const mismatch of [
      { draft: { ...recoveryDraft(), id: 'other-batch' }, artifact: artifactView() },
      { draft: { ...recoveryDraft(), project_id: 'other-project' }, artifact: artifactView() },
      { draft: recoveryDraft(), artifact: { ...artifactView(), id: 'other-artifact' } },
    ]) {
      wrapper.unmount()
      sessionStorage.setItem('tlc-batch-candidate.active.v1', JSON.stringify({ project_id: 'project-from-context', batch_id: 'batch-server' }))
      draftResponse = mismatch.draft
      artifactResponse = mismatch.artifact
      const next = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true } })
      await flushPromises()
      await next.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
      await flushPromises()
      await next.get('[data-testid="restore-batch-draft"]').trigger('click')
      await flushPromises()
      expect(sessionStorage.getItem('tlc-batch-candidate.active.v1')).toBeNull()
      expect(next.findAll('[data-testid="restore-batch-draft"]')).toHaveLength(0)
      next.unmount()
    }
  })
})
