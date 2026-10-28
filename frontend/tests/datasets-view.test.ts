import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DataCenterView from '@/modules/datasets/DataCenterView.vue'
import { useAppStore } from '@/stores/app'
import { useConfirmationStore } from '@/stores/confirmations'

const domain = {
  id: 'domain-tlc', version: 'tlc-v1', chromatography_type: 'tlc',
  record_schema: { rf: { type: 'number', unit: null, required: true } }, unit_definitions: {},
  identity_fields: ['external_id'], deduplication_policy: 'reject', supported_import_modes: ['append'],
}

function response(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ data, warnings: [], request_id: 'datasets-view-test' }), { status, headers: { 'content-type': 'application/json', ...headers } })
}

const committedVersion = {
  id: 'version-1', project_id: 'project-1', domain_id: 'domain-tlc', version: 1, sha256: 'a'.repeat(64), status: 'READY',
  record_count: 3, source_import_id: 'import-1', snapshot_artifact_id: 'snapshot-1', schema_version: 'tlc-v1',
  quality_report_ref: { type: 'artifact', id: 'quality-1' }, created_at: '2026-10-26T00:00:00Z', referenced_by_count: 0,
}

async function mountCommittedImport(versionId: string | null, versionDetail = committedVersion) {
  const requests: Array<{ url: string; method: string }> = []
  const counts = { new: 3, updated: 0, unchanged: 0, invalid: 0, duplicate: 0 }
  const committedImport = {
    id: 'import-1', project_id: 'project-1', domain_id: 'domain-tlc', artifact_id: 'source-1', schema_version: 'tlc-v1', import_mode: 'append',
    field_mapping: {}, source_description: 'synthetic fixture', revision: 1, config_hash: 'b'.repeat(64), etag: 'etag-1', status: 'COMMITTED',
    job_id: 'job-commit-1', validation_ref: 'validation-1', new_data_version_id: versionId, request_snapshot: null,
    created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z',
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
    if (url.endsWith('/data-domains')) return response([domain])
    if (url.includes('/data-domains/domain-tlc/schema')) return response(domain)
    if (url.includes('/data-imports/import-1/validation')) return response({
      id: 'validation-1', import_id: 'import-1', revision: 1, config_hash: 'b'.repeat(64), status: 'VALID', counts,
      report_artifact_id: null, warnings: [], parser_revision: 'parser-1', parser_hash: 'c'.repeat(64), created_at: '2026-10-26T00:00:00Z',
    })
    if (url.includes('/data-imports/import-1')) return response(committedImport)
    if (url.includes('/data-versions?')) return response([committedVersion])
    if (url.endsWith('/data-versions/version-1')) return response(versionDetail)
    throw new Error(`Unexpected request ${method} ${url}`)
  }))
  localStorage.setItem('chrom-platform.datasets.import-pointer', JSON.stringify({ actorId: 'actor-1', projectId: 'project-1', domainId: 'domain-tlc', importId: 'import-1' }))
  const pinia = createPinia(); setActivePinia(pinia)
  const app = useAppStore()
  app.state = 'ready'
  app.accessContext = {
    actor: { id: 'actor-1', auth_mode: 'deployment_default', policy_version: 'p1' }, actor_id: 'actor-1', auth_mode: 'deployment_default',
    allowed_actions: ['*'], permissions: [], accessible_projects: [{ id: 'project-1', name: '测试项目' }],
    default_project_id: 'project-1', project_selection: 'resolved', is_multi_user_authenticated: false,
  }
  const wrapper = mount(DataCenterView, { global: { plugins: [pinia] } })
  await flushPromises(); await flushPromises(); await flushPromises()
  return { wrapper, requests }
}

beforeEach(() => {
  setActivePinia(createPinia()); vi.restoreAllMocks()
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) })
})

describe('data center view', () => {
  it('uses the access-context project and schema, and binds only a server-ready uploaded artifact', async () => {
    const requests: Array<{ url: string; method: string; body?: BodyInit | null }> = []
    let artifactReads = 0
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'
      requests.push({ url, method, body: init?.body })
      if (url.endsWith('/data-domains')) return response([domain])
      if (url.includes('/data-domains/domain-tlc/schema')) return response(domain)
      if (url.endsWith('/artifacts/uploads') && method === 'POST') return response({ id: 'upload-1', artifact_id: 'artifact-1', status: 'CREATED', content_url: '/artifacts/uploads/upload-1/content', allowed_media_types: ['text/csv'], max_size_bytes: 1000, received_bytes: 0, expires_at: '2026-10-27T00:00:00Z' })
      if (url.endsWith('/artifacts/uploads/upload-1/content') && method === 'PUT') return response({ artifact_id: 'artifact-1', received_at: '2026-10-26T00:00:00Z', received_bytes: 8, sha256: 'a'.repeat(64) })
      if (url.endsWith('/artifacts/uploads/upload-1/complete') && method === 'POST') return response({ id: 'artifact-1', artifact_id: 'artifact-1', job_id: 'job-1', resource_ref: { type: 'artifact', id: 'artifact-1' }, status: 'VALIDATING' })
      if (url.endsWith('/artifacts/artifact-1')) {
        artifactReads += 1
        return response({ id: 'artifact-1', project_id: 'project-1', owner_id: 'actor-1', kind: 'dataset_package', file_name: 'data.csv', media_type: 'text/csv', size_bytes: 8, sha256: 'a'.repeat(64), status: artifactReads === 1 ? 'VALIDATING' : 'AVAILABLE', scan_status: artifactReads === 1 ? 'PENDING' : 'PASSED', validation_report_ref: null, source_ref: null, download_allowed: false, warnings: [], created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z' })
      }
      throw new Error(`Unexpected request ${method} ${url}`)
    }))
    const pinia = createPinia(); setActivePinia(pinia)
    const app = useAppStore()
    app.state = 'ready'
    app.accessContext = {
      actor: { id: 'actor-1', auth_mode: 'deployment_default', policy_version: 'p1' }, actor_id: 'actor-1', auth_mode: 'deployment_default',
      allowed_actions: ['*'], permissions: [], accessible_projects: [{ id: 'project-1', name: '测试项目' }],
      default_project_id: 'project-1', project_selection: 'resolved', is_multi_user_authenticated: false,
    }
    const wrapper = mount(DataCenterView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(wrapper.text()).toContain('测试项目')
    expect(wrapper.find('[data-testid="datasets-domain"]').exists()).toBe(true)
    await wrapper.get('[data-testid="datasets-domain"]').setValue('domain-tlc')
    await flushPromises()
    expect(wrapper.text()).toContain('external_id')
    expect(wrapper.text()).toContain('是否适用于实际科学数据由服务端导入校验决定')
    expect((wrapper.get('[data-testid="datasets-upload"]').element as HTMLButtonElement).disabled).toBe(true)
    expect((wrapper.get('[data-testid="datasets-create-import"]').element as HTMLButtonElement).disabled).toBe(true)
    const input = wrapper.get('[data-testid="datasets-file"]')
    const selectedFile = new File(['rf\n0.5'], 'data.csv', { type: 'text/csv' })
    Object.defineProperty(input.element, 'files', { configurable: true, value: [selectedFile] })
    await input.trigger('change')
    await wrapper.get('[data-testid="datasets-upload"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('VALIDATING')
    expect(wrapper.text()).toContain('PENDING')
    expect((wrapper.get('[data-testid="datasets-create-import"]').element as HTMLButtonElement).disabled).toBe(true)
    const refreshButton = wrapper.findAll('button').find((button) => button.text().includes('刷新制品状态'))
    expect(refreshButton).toBeDefined()
    await refreshButton!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('AVAILABLE')
    expect(wrapper.text()).toContain('PASSED')
    expect((wrapper.get('[data-testid="datasets-create-import"]').element as HTMLButtonElement).disabled).toBe(false)
    const createUpload = requests.find((request) => request.url.endsWith('/artifacts/uploads') && request.method === 'POST')
    expect(JSON.parse(String(createUpload?.body))).toMatchObject({ project_id: 'project-1', kind: 'dataset_package', file_name: 'data.csv' })
    wrapper.unmount()
  })

  it('restores only the actor/project/domain/import pointer with business GETs after a reload', async () => {
    const requests: Array<{ url: string; method: string }> = []
    const savedImport = {
      id: 'import-1', project_id: 'project-1', domain_id: 'domain-tlc', artifact_id: 'artifact-1', schema_version: 'tlc-v1', import_mode: 'append',
      field_mapping: {}, source_description: 'synthetic fixture', revision: 1, config_hash: 'b'.repeat(64), etag: 'etag-1', status: 'DRAFT',
      job_id: null, validation_ref: null, new_data_version_id: null, request_snapshot: null,
      created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z',
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/data-domains')) return response([domain])
      if (url.includes('/data-domains/domain-tlc/schema')) return response(domain)
      if (url.includes('/data-imports/import-1')) return response(savedImport)
      throw new Error(`Unexpected request ${method} ${url}`)
    }))
    localStorage.setItem('chrom-platform.datasets.import-pointer', JSON.stringify({ actorId: 'actor-1', projectId: 'project-1', domainId: 'domain-tlc', importId: 'import-1' }))
    const pinia = createPinia(); setActivePinia(pinia)
    const app = useAppStore()
    app.state = 'ready'
    app.accessContext = {
      actor: { id: 'actor-1', auth_mode: 'deployment_default', policy_version: 'p1' }, actor_id: 'actor-1', auth_mode: 'deployment_default',
      allowed_actions: ['*'], permissions: [], accessible_projects: [{ id: 'project-1', name: '测试项目' }],
      default_project_id: 'project-1', project_selection: 'resolved', is_multi_user_authenticated: false,
    }

    const wrapper = mount(DataCenterView, { global: { plugins: [pinia] } })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('导入草稿')
    expect(wrapper.text()).toContain('DRAFT')
    expect(requests.filter((request) => request.url.includes('/data-imports/import-1')).every((request) => request.method === 'GET')).toBe(true)
    expect(requests.some((request) => request.method === 'POST')).toBe(false)
    expect(JSON.parse(localStorage.getItem('chrom-platform.datasets.import-pointer') ?? '{}')).toEqual({ actorId: 'actor-1', projectId: 'project-1', domainId: 'domain-tlc', importId: 'import-1' })
    wrapper.unmount()
    localStorage.removeItem('chrom-platform.datasets.import-pointer')
  })

  it('keeps an INVALID draft on create failure and clears its validation/report/confirmation after a new draft succeeds', async () => {
    const requests: Array<{ url: string; method: string }> = []
    let resolveReport!: (result: Response) => void
    const pendingReport = new Promise<Response>((resolve) => { resolveReport = resolve })
    let createAttempts = 0
    const counts = { new: 0, updated: 0, unchanged: 0, invalid: 4944, duplicate: 0 }
    const invalidImport = {
      id: 'import-old', project_id: 'project-1', domain_id: 'domain-tlc', artifact_id: 'source-old', schema_version: 'tlc-v1', import_mode: 'append',
      field_mapping: {}, source_description: 'synthetic INVALID fixture', revision: 3, config_hash: 'b'.repeat(64), etag: 'etag-old', status: 'INVALID',
      job_id: null, validation_ref: 'validation-old', new_data_version_id: null, request_snapshot: null,
      created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z',
    }
    const newImport = {
      id: 'import-new', project_id: 'project-1', domain_id: 'domain-tlc', artifact_id: 'artifact-new', schema_version: 'tlc-v1', import_mode: 'append',
      field_mapping: {}, source_description: 'synthetic new draft', revision: 1, config_hash: 'c'.repeat(64), etag: 'etag-new', status: 'DRAFT',
      job_id: null, validation_ref: null, new_data_version_id: null, request_snapshot: null,
      created_at: '2026-10-27T00:00:00Z', updated_at: '2026-10-27T00:00:00Z',
    }
    const invalidReport = {
      report_schema_version: 'data-import-validation-report-1', status: 'INVALID', project_id: 'project-1', import_id: 'import-old', revision: 3,
      config_hash: 'b'.repeat(64), domain_id: 'domain-tlc', domain_schema_version: 'tlc-v1', source_artifact_id: 'source-old', source_sha256: 'a'.repeat(64),
      encoding: 'utf-8', parser_revision: 'parser-1', row_count: 4944, counts, issues: [], total_issue_count: 4944, issues_truncated: true,
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/data-domains')) return response([domain])
      if (url.includes('/data-domains/domain-tlc/schema')) return response(domain)
      if (url.includes('/data-imports/import-old/validation')) return response({ id: 'validation-old', import_id: 'import-old', revision: 3, config_hash: 'b'.repeat(64), status: 'INVALID', counts, report_artifact_id: 'report-old', warnings: [], parser_revision: 'parser-1', parser_hash: 'd'.repeat(64), created_at: '2026-10-26T00:00:00Z' })
      if (url.endsWith('/artifacts/report-old')) return response({ id: 'report-old', project_id: 'project-1', owner_id: 'actor-1', kind: 'export_report', file_name: 'report.json', media_type: 'application/json', size_bytes: 1024, sha256: 'e'.repeat(64), status: 'AVAILABLE', scan_status: 'NOT_CONFIGURED', source_ref: { type: 'data_import_validation', id: 'validation-old' }, validation_report_ref: null, download_allowed: true, warnings: [], created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z' })
      if (url.endsWith('/artifacts/source-old')) return response({ id: 'source-old', project_id: 'project-1', owner_id: 'actor-1', kind: 'dataset_package', file_name: 'old.csv', media_type: 'text/csv', size_bytes: 8, sha256: 'a'.repeat(64), status: 'AVAILABLE', scan_status: 'PASSED', source_ref: null, validation_report_ref: null, download_allowed: false, warnings: [], created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z' })
      if (url.endsWith('/artifacts/report-old/download')) return pendingReport
      if (url.endsWith('/artifacts/uploads') && method === 'POST') return response({ id: 'upload-new', artifact_id: 'artifact-new', status: 'CREATED', content_url: '/artifacts/uploads/upload-new/content', allowed_media_types: ['text/csv'], max_size_bytes: 1000, received_bytes: 0, expires_at: '2026-10-28T00:00:00Z' })
      if (url.endsWith('/artifacts/uploads/upload-new/content') && method === 'PUT') return response({ artifact_id: 'artifact-new', received_at: '2026-10-27T00:00:00Z', received_bytes: 8, sha256: 'f'.repeat(64) })
      if (url.endsWith('/artifacts/uploads/upload-new/complete') && method === 'POST') return response({ id: 'artifact-new', artifact_id: 'artifact-new', job_id: 'upload-job-new', resource_ref: { type: 'artifact', id: 'artifact-new' }, status: 'VALIDATING' })
      if (url.endsWith('/artifacts/artifact-new')) return response({ id: 'artifact-new', project_id: 'project-1', owner_id: 'actor-1', kind: 'dataset_package', file_name: 'new.csv', media_type: 'text/csv', size_bytes: 8, sha256: 'f'.repeat(64), status: 'AVAILABLE', scan_status: 'PASSED', validation_report_ref: null, source_ref: null, download_allowed: false, warnings: [], created_at: '2026-10-27T00:00:00Z', updated_at: '2026-10-27T00:00:00Z' })
      if (url.includes('/data-imports/import-old')) return response(invalidImport)
      if (url.endsWith('/data-imports') && method === 'POST') {
        createAttempts += 1
        return createAttempts === 1 ? response({ code: 'TEMPORARY_FAILURE' }, 500) : response(newImport, 201)
      }
      throw new Error(`Unexpected request ${method} ${url}`)
    }))
    localStorage.setItem('chrom-platform.datasets.import-pointer', JSON.stringify({ actorId: 'actor-1', projectId: 'project-1', domainId: 'domain-tlc', importId: 'import-old' }))
    const pinia = createPinia(); setActivePinia(pinia)
    const app = useAppStore()
    app.state = 'ready'
    app.accessContext = {
      actor: { id: 'actor-1', auth_mode: 'deployment_default', policy_version: 'p1' }, actor_id: 'actor-1', auth_mode: 'deployment_default',
      allowed_actions: ['*'], permissions: [], accessible_projects: [{ id: 'project-1', name: '测试项目' }],
      default_project_id: 'project-1', project_selection: 'resolved', is_multi_user_authenticated: false,
    }
    const wrapper = mount(DataCenterView, { global: { plugins: [pinia] } })
    await flushPromises(); await flushPromises(); await flushPromises()
    expect(wrapper.text()).toContain('Validation INVALID')
    expect(wrapper.text()).toContain('4944')

    const fileInput = wrapper.get('[data-testid="datasets-file"]')
    Object.defineProperty(fileInput.element, 'files', { configurable: true, value: [new File(['rf\n0.5'], 'new.csv', { type: 'text/csv' })] })
    await fileInput.trigger('change')
    await wrapper.get('[data-testid="datasets-upload"]').trigger('click')
    await flushPromises(); await flushPromises()
    expect((wrapper.get('[data-testid="datasets-create-import"]').element as HTMLButtonElement).disabled).toBe(false)

    const confirmationStore = useConfirmationStore(pinia)
    confirmationStore.confirmation = { id: 'old-confirmation', project_id: 'project-1', actor_id: 'actor-1', resource_ref: { id: 'import-old', type: 'data_import' }, action: 'data_import.commit', revision: 3, config_hash: 'b'.repeat(64), policy_version: 'p1', status: 'CONFIRMED', created_at: '2026-10-26T00:00:00Z', expires_at: '2026-10-27T00:00:00Z', consumed_at: null, consumed_result_ref: null, summary_snapshot: {} }
    await wrapper.get('[data-testid="datasets-create-import"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Validation INVALID')
    expect(wrapper.text()).toContain('4944')

    await wrapper.get('[data-testid="datasets-create-import"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('DRAFT')
    expect(wrapper.text()).toContain('当前草稿尚无关联 validation')
    expect(wrapper.text()).not.toContain('Validation INVALID')
    expect(wrapper.text()).not.toContain('4944')
    expect(confirmationStore.confirmation).toBeNull()
    expect(JSON.parse(localStorage.getItem('chrom-platform.datasets.import-pointer') ?? '{}').importId).toBe('import-new')
    expect(requests.filter((request) => request.method === 'POST' && request.url.includes('/data-imports/')).map((request) => request.url)).toEqual([])
    expect(requests.filter((request) => request.method === 'POST' && /\/data-imports\/?$/.test(request.url))).toHaveLength(2)

    resolveReport(new Response(JSON.stringify(invalidReport), { status: 200, headers: { 'content-type': 'application/octet-stream' } }))
    await flushPromises(); await flushPromises()
    expect(wrapper.text()).toContain('DRAFT')
    expect(wrapper.text()).not.toContain('Validation INVALID')
    expect(wrapper.text()).not.toContain('4944')
    expect(requests.some((request) => request.method === 'POST' && /\/(validate|commit)(\?|$)/.test(request.url))).toBe(false)
    wrapper.unmount()
    localStorage.removeItem('chrom-platform.datasets.import-pointer')
  })

  it('allows the server confirmation binding hash to differ from draft config while preserving hash, revision and etag checks', async () => {
    const requests: Array<{ url: string; method: string; body?: BodyInit | null; headers?: HeadersInit }> = []
    let importReads = 0
    const counts = { new: 2, updated: 0, unchanged: 0, invalid: 0, duplicate: 0 }
    const draftConfigHash = 'b'.repeat(64)
    const serverBindingHash = 'e'.repeat(64)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method, body: init?.body, headers: init?.headers })
      if (url.endsWith('/data-domains')) return response([domain])
      if (url.includes('/data-domains/domain-tlc/schema')) return response(domain)
      if (url.includes('/data-imports/import-1/validate') && method === 'POST') return response({ id: 'import-1', job_id: 'job-1', resource_ref: { type: 'data_import', id: 'import-1' }, status: 'QUEUED' }, 202, { location: '/api/v1/data-imports/import-1' })
      if (url.endsWith('/confirmations/previews') && method === 'POST') return response({
        resource_ref: { id: 'import-1', type: 'data_import' }, action: 'data_import.commit', revision: 1,
        config_hash: serverBindingHash, etag: 'etag-1', policy_version: 'p1', summary: [], dependencies: [], impact: {}, can_confirm: true, blockers: [],
      })
      if (url.endsWith('/confirmations') && method === 'POST') return response({
        id: 'confirmation-1', project_id: 'project-1', actor_id: 'actor-1', resource_ref: { id: 'import-1', type: 'data_import' }, action: 'data_import.commit',
        revision: 1, config_hash: serverBindingHash, policy_version: 'p1', status: 'CONFIRMED', created_at: '2026-10-26T00:00:00Z', expires_at: '2026-10-27T00:00:00Z',
        consumed_at: null, consumed_result_ref: null, summary_snapshot: {},
      }, 201, { etag: 'confirmation-etag' })
      if (url.includes('/data-imports/import-1/validation')) return response({ id: 'validation-1', import_id: 'import-1', revision: 1, config_hash: draftConfigHash, status: 'VALID', counts, report_artifact_id: null, warnings: [], parser_revision: 'parser-1', parser_hash: 'c'.repeat(64), created_at: '2026-10-26T00:00:00Z' })
      if (url.includes('/data-imports/import-1')) {
        importReads += 1
        const active = importReads === 3
        return response({ id: 'import-1', project_id: 'project-1', domain_id: 'domain-tlc', artifact_id: 'artifact-1', schema_version: 'tlc-v1', import_mode: 'append', field_mapping: {}, source_description: 'synthetic fixture', revision: 1, config_hash: draftConfigHash, etag: 'etag-1', status: active ? 'VALIDATING' : active ? 'VALIDATING' : importReads < 3 ? 'DRAFT' : 'READY', job_id: importReads < 3 ? null : 'job-1', validation_ref: importReads >= 4 ? 'validation-1' : null, new_data_version_id: null, request_snapshot: null, created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z' })
      }
      if (url.endsWith('/jobs/job-1')) return response({ id: 'job-1', project_id: 'project-1', kind: 'data_import_validation', resource_ref: { type: 'data_import', id: 'import-1' }, attempt: 1, status: 'SUCCEEDED', cancelable: false, created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z', started_at: null, finished_at: null, deadline_at: null, etag: 'job-etag', error: null, progress: {}, result_ref: null, resume_mode: 'none', retryable: false, revision: 1, owner_id: 'actor-1' })
      throw new Error(`Unexpected request ${method} ${url}`)
    }))
    localStorage.setItem('chrom-platform.datasets.import-pointer', JSON.stringify({ actorId: 'actor-1', projectId: 'project-1', domainId: 'domain-tlc', importId: 'import-1' }))
    const pinia = createPinia(); setActivePinia(pinia)
    const app = useAppStore()
    app.state = 'ready'
    app.accessContext = {
      actor: { id: 'actor-1', auth_mode: 'deployment_default', policy_version: 'p1' }, actor_id: 'actor-1', auth_mode: 'deployment_default',
      allowed_actions: ['*'], permissions: [], accessible_projects: [{ id: 'project-1', name: '测试项目' }],
      default_project_id: 'project-1', project_selection: 'resolved', is_multi_user_authenticated: false,
    }
    const wrapper = mount(DataCenterView, { global: { plugins: [pinia] } })
    await flushPromises(); await flushPromises()
    const validateButton = wrapper.findAll('button').find((button) => button.text().includes('执行服务端校验'))
    expect(validateButton).toBeDefined()
    await validateButton!.trigger('click')
    await flushPromises(); await flushPromises()

    expect(wrapper.text()).toContain('READY')
    expect(wrapper.text()).toContain('Validation VALID')
    expect(wrapper.text()).toContain('校验差异预计：拟新增 2')
    expect(wrapper.text()).toContain('提交前确认')
    const submitButton = () => wrapper.findAll('button').find((button) => button.text().includes('提交导入'))!
    expect((submitButton().element as HTMLButtonElement).disabled).toBe(true)
    expect(requests.filter((request) => request.method === 'POST' && request.url.includes('/data-imports/import-1/validate'))).toHaveLength(1)
    expect(requests.some((request) => request.url.endsWith('/jobs/job-1'))).toBe(true)
    const previewButton = wrapper.findAll('button').find((button) => button.text().includes('读取服务端预览'))
    expect(previewButton).toBeDefined()
    await previewButton!.trigger('click')
    await flushPromises()
    expect(useConfirmationStore(pinia).preview?.config_hash).toBe(serverBindingHash)
    expect(useConfirmationStore(pinia).preview?.config_hash).not.toBe(draftConfigHash)
    const confirmButton = wrapper.findAll('button').find((button) => button.text().includes('确认此版本'))
    expect(confirmButton).toBeDefined()
    await confirmButton!.trigger('click')
    await flushPromises()
    const confirmationRequest = requests.find((request) => request.url.endsWith('/confirmations') && request.method === 'POST')
    expect(JSON.parse(String(confirmationRequest?.body))).toMatchObject({ revision: 1, config_hash: serverBindingHash })
    expect((submitButton().element as HTMLButtonElement).disabled).toBe(false)
    const confirmationStore = useConfirmationStore(pinia)
    confirmationStore.confirmation = { ...confirmationStore.confirmation!, config_hash: 'f'.repeat(64) }
    await flushPromises()
    expect((submitButton().element as HTMLButtonElement).disabled).toBe(true)
    expect(requests.filter((request) => request.method === 'POST' && request.url.includes('/commit'))).toHaveLength(0)
    wrapper.unmount()
    localStorage.removeItem('chrom-platform.datasets.import-pointer')
  })

  it('restores a STALE validation report for its own revision without exposing confirmation or commit', async () => {
    const requests: Array<{ url: string; method: string }> = []
    let importReads = 0
    const counts = { new: 1, updated: 0, unchanged: 0, invalid: 0, duplicate: 0 }
    const staleImport = {
      id: 'import-1', project_id: 'project-1', domain_id: 'domain-tlc', artifact_id: 'source-1', schema_version: 'tlc-v1', import_mode: 'append', field_mapping: {}, source_description: 'synthetic fixture',
      revision: 2, config_hash: 'f'.repeat(64), etag: 'etag-2', status: 'DRAFT', job_id: null, validation_ref: null, new_data_version_id: null,
      request_snapshot: null, created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z',
    }
    const staleReport = {
      report_schema_version: 'data-import-validation-report-1', status: 'VALID', project_id: 'project-1', import_id: 'import-1', revision: 1,
      config_hash: 'b'.repeat(64), domain_id: 'domain-tlc', domain_schema_version: 'tlc-v1', source_artifact_id: 'source-1', source_sha256: 'a'.repeat(64),
      encoding: 'utf-8', parser_revision: 'parser-1', row_count: 1, counts, issues: [], total_issue_count: 0, issues_truncated: false,
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/data-domains')) return response([domain])
      if (url.includes('/data-domains/domain-tlc/schema')) return response(domain)
      if (url.includes('/data-imports/import-1/validation')) return response({ id: 'validation-old', import_id: 'import-1', revision: 1, config_hash: 'b'.repeat(64), status: 'STALE', counts, report_artifact_id: 'report-old', warnings: [], parser_revision: 'parser-1', parser_hash: 'c'.repeat(64), created_at: '2026-10-25T00:00:00Z' })
      if (url.endsWith('/artifacts/report-old')) return response({ id: 'report-old', project_id: 'project-1', owner_id: 'actor-1', kind: 'export_report', file_name: 'report.json', media_type: 'application/json', size_bytes: 512, sha256: 'd'.repeat(64), status: 'AVAILABLE', scan_status: 'NOT_CONFIGURED', source_ref: { type: 'data_import_validation', id: 'validation-old' }, validation_report_ref: null, download_allowed: true, warnings: [], created_at: '2026-10-25T00:00:00Z', updated_at: '2026-10-25T00:00:00Z' })
      if (url.endsWith('/artifacts/report-old/download')) return new Response(JSON.stringify(staleReport), { status: 200, headers: { 'content-type': 'application/octet-stream' } })
      if (url.endsWith('/artifacts/source-1')) return response({ id: 'source-1', project_id: 'project-1', owner_id: 'actor-1', kind: 'dataset_package', file_name: 'input.csv', media_type: 'text/csv', size_bytes: 8, sha256: 'a'.repeat(64), status: 'AVAILABLE', scan_status: 'PASSED', source_ref: null, validation_report_ref: null, download_allowed: false, warnings: [], created_at: '2026-10-25T00:00:00Z', updated_at: '2026-10-25T00:00:00Z' })
      if (url.includes('/data-imports/import-1')) { importReads += 1; return response(staleImport) }
      throw new Error(`Unexpected request ${method} ${url}`)
    }))
    localStorage.setItem('chrom-platform.datasets.import-pointer', JSON.stringify({ actorId: 'actor-1', projectId: 'project-1', domainId: 'domain-tlc', importId: 'import-1' }))
    const pinia = createPinia(); setActivePinia(pinia)
    const app = useAppStore()
    app.state = 'ready'
    app.accessContext = {
      actor: { id: 'actor-1', auth_mode: 'deployment_default', policy_version: 'p1' }, actor_id: 'actor-1', auth_mode: 'deployment_default',
      allowed_actions: ['*'], permissions: [], accessible_projects: [{ id: 'project-1', name: '测试项目' }],
      default_project_id: 'project-1', project_selection: 'resolved', is_multi_user_authenticated: false,
    }
    const wrapper = mount(DataCenterView, { global: { plugins: [pinia] } })
    await flushPromises(); await flushPromises(); await flushPromises()

    expect(importReads).toBeGreaterThanOrEqual(2)
    expect(wrapper.text()).toContain('Validation STALE')
    expect(wrapper.text()).toContain('这是所属 validation 的历史报告')
    expect(wrapper.text()).toContain('报告状态 VALID')
    expect(wrapper.text()).toContain('该 VALID 报告列出 0 条问题；这是校验结果，不代表已提交。')
    expect(wrapper.text()).not.toContain('提交前确认')
    expect(wrapper.findAll('button').some((button) => button.text().includes('提交导入'))).toBe(false)
    expect(requests.some((request) => request.url.endsWith('/artifacts/report-old/download'))).toBe(true)
    expect(requests.some((request) => request.url.endsWith('/artifacts/source-1'))).toBe(true)
    wrapper.unmount()
    localStorage.removeItem('chrom-platform.datasets.import-pointer')
  })

  it('restores a committed import and its nonempty immutable version detail after reload using GET only', async () => {
    const { wrapper, requests } = await mountCommittedImport('version-1')

    expect(wrapper.text()).toContain('COMMITTED')
    expect(wrapper.text()).toContain('READY')
    expect(wrapper.text()).toContain('提交完成：该不可变版本实际包含 3 条记录。')
    expect(wrapper.text()).toContain('校验差异预计：拟新增 3')
    expect(wrapper.text()).toContain('实际记录数')
    expect(wrapper.text()).toContain('来源导入')
    expect(wrapper.text()).toContain('import-1')
    expect(wrapper.text()).toContain('快照制品引用')
    expect(wrapper.text()).toContain('snapshot-1')
    expect(wrapper.text()).toContain('quality-1')
    expect(requests.some((request) => request.url.endsWith('/data-versions/version-1'))).toBe(true)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
    localStorage.removeItem('chrom-platform.datasets.import-pointer')
  })

  it.each([
    ['missing committed version id', null, committedVersion, '未提供 new_data_version_id'],
    ['version from another import', 'version-1', { ...committedVersion, source_import_id: 'import-other' }, '来源导入不匹配'],
    ['empty version', 'version-1', { ...committedVersion, record_count: 0 }, '不符合本阶段的非空版本要求'],
  ])('does not report committed-version success for %s and never retries with POST', async (_case, versionId, versionDetail, errorText) => {
    const { wrapper, requests } = await mountCommittedImport(versionId, versionDetail as typeof committedVersion)

    expect(wrapper.text()).toContain(errorText)
    expect(wrapper.text()).not.toContain('提交完成：该不可变版本实际包含')
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
    localStorage.removeItem('chrom-platform.datasets.import-pointer')
  })
})
