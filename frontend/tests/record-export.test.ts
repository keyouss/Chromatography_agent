import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PredictionRecordsView from '@/modules/records/PredictionRecordsView.vue'
import { useRecordExportStore } from '@/modules/records/export-store'
import { useAppStore } from '@/stores/app'
import type { ContractCreateRecordExportBody } from '@/core/contract-types'

const record = {
  id: 'record-export-1', project_id: 'project-export', prediction_id: null, batch_id: null,
  source: 'web', actor_id: 'actor-private', chromatography_type: 'tlc', model_snapshot: {}, input_snapshot: {},
  result_ref: null, warnings: [], status: 'SUCCEEDED', archived_at: null, created_at: '2026-10-03T10:00:00Z',
  artifact_refs: [], revision: 1, etag: 'record-etag',
}
const record2 = { ...record, id: 'record-export-2', prediction_id: null, etag: 'record-etag-2' }
const exportId = 'export-1'
const jobId = 'job-export-1'

function envelope(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ data, request_id: 'export-test', warnings: [] }), { status, headers: { 'content-type': 'application/json', ...headers } })
}

describe('prediction record export UI', () => {
  let requests: { url: string; method: string; headers: Headers; body: unknown }[]
  let exportStatus: string
  let jobStatus: string
  let retryable: boolean
  let cancelable: boolean
  let artifactStatus: string
  let artifactDownloadAllowed: boolean
  let failExportPost = false
  let emptyRecordList = false
  let exportFormats: string[]
  let capabilityGate: Promise<Response> | null
  let exportReadFailure: number | null
  let jobReadFailure: number | null
  let artifactReadFailure: number | null
  let artifactDownloadFailure: number | null
  let exportReadNetworkFailure: boolean
  let exportAcceptanceFault: 'none' | 'resource_ref' | 'location' | 'status' | 'missing_location' | 'external_location' | 'prefix_location' | 'query_location' | 'hash_location' | 'same_origin_absolute' | 'path_relative_location' | 'view'

  beforeEach(() => {
    sessionStorage.clear(); requests = []; exportStatus = 'RUNNING'; jobStatus = 'RUNNING'; retryable = false; cancelable = true; artifactStatus = 'AVAILABLE'; artifactDownloadAllowed = true; failExportPost = false; emptyRecordList = false; exportFormats = ['json', 'csv', 'xlsx']; capabilityGate = null; exportReadFailure = null; jobReadFailure = null; artifactReadFailure = null; artifactDownloadFailure = null; exportReadNetworkFailure = false; exportAcceptanceFault = 'none'
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'; const headers = new Headers(init?.headers); let body: unknown = null
      if (typeof init?.body === 'string') { try { body = JSON.parse(init.body) } catch { body = init.body } }
      requests.push({ url, method, headers, body })
      if (url.endsWith('/system/capabilities')) return capabilityGate ?? envelope({ build_id: 'universal-v1', api_version: '1.0', modules: [], chromatography_types: [], assistant_modes: [], export_formats: exportFormats, auth_mode: 'deployment_default', data_origin: 'production', capabilities: [] })
      if (url.endsWith('/system/version')) return envelope({ version: '1.0' })
      if (url.endsWith('/system/access-context')) return envelope({ project_selection: 'resolved', default_project_id: 'project-export', accessible_projects: [{ id: 'project-export', name: 'Export Project' }] })
      if (url.includes('/prediction-records?')) return new Response(JSON.stringify({ data: emptyRecordList ? [] : [record, record2], page: { limit: 20, has_more: false, next_cursor: null }, request_id: 'records', warnings: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
      if (url === '/api/v1/exports' && method === 'POST') {
        if (failExportPost) { failExportPost = false; return new Response(JSON.stringify({ error: { code: 'SERVICE_UNAVAILABLE', message: 'secret internal detail' } }), { status: 503, headers: { 'content-type': 'application/json' } }) }
        const accepted = { id: exportId, job_id: jobId, status: exportAcceptanceFault === 'status' ? 'UNKNOWN' : 'QUEUED', resource_ref: { type: exportAcceptanceFault === 'resource_ref' ? 'prediction_record' : 'export', id: exportId } }
        const headers: Record<string, string> = {}
        if (exportAcceptanceFault !== 'missing_location') {
          const expectedPath = `/api/v1/exports/${exportAcceptanceFault === 'location' ? 'wrong-export' : exportId}`
          headers.Location = exportAcceptanceFault === 'external_location' ? `https://outside.example${expectedPath}`
            : exportAcceptanceFault === 'prefix_location' ? `/forward${expectedPath}`
              : exportAcceptanceFault === 'query_location' ? `${expectedPath}?next=/`
                : exportAcceptanceFault === 'hash_location' ? `${expectedPath}#fragment`
                  : exportAcceptanceFault === 'same_origin_absolute' ? `${window.location.origin}${expectedPath}`
                    : exportAcceptanceFault === 'path_relative_location' ? `exports/${exportId}`
                      : expectedPath
        }
        return envelope(accepted, exportAcceptanceFault === 'status' ? 200 : 202, headers)
      }
      if (url.endsWith(`/exports/${exportId}`)) {
        if (exportReadNetworkFailure) throw new Error('secret transport detail')
        if (exportReadFailure) return new Response(JSON.stringify({ error: { code: 'SECRET_ERROR', message: 'secret internal detail' } }), { status: exportReadFailure, headers: { 'content-type': 'application/json' } })
        const view = { id: exportId, job_id: jobId, status: exportStatus, format: 'csv', format_version: 'v1', source_snapshots: {}, artifact_id: exportStatus === 'SUCCEEDED' ? 'artifact-export-1' : null, error: null, created_at: '2026-10-03T10:00:00Z', completed_at: exportStatus === 'SUCCEEDED' ? '2026-10-03T10:00:02Z' : null }
        return envelope(exportAcceptanceFault === 'view' ? { ...view, unexpected: true } : view)
      }
      if (url.endsWith(`/jobs/${jobId}`)) {
        if (jobReadFailure) return new Response(JSON.stringify({ error: { code: 'SECRET_ERROR', message: 'secret internal detail' } }), { status: jobReadFailure, headers: { 'content-type': 'application/json' } })
        return envelope({ id: jobId, project_id: 'project-export', owner_id: 'actor-private', kind: 'export', resource_ref: { type: 'export', id: exportId }, status: jobStatus, attempt: 1, progress: {}, cancelable, retryable, resume_mode: 'none', created_at: '', updated_at: '', started_at: null, finished_at: null, deadline_at: null, result_ref: null, error: null, revision: 1, etag: '"job-etag-v1"' })
      }
      if (url.endsWith(`/jobs/${jobId}/cancel`) && method === 'POST') { expect(body).toEqual({ reason: null }); jobStatus = 'CANCEL_REQUESTED'; exportStatus = 'RUNNING'; cancelable = false; return envelope({ id: jobId, project_id: 'project-export', status: jobStatus, etag: '"job-etag-v2"' }, 202) }
      if (url.endsWith(`/jobs/${jobId}/retry`) && method === 'POST') { jobStatus = 'QUEUED'; exportStatus = 'RUNNING'; retryable = false; return envelope({ id: jobId, project_id: 'project-export', status: jobStatus, etag: '"job-etag-v2"' }, 202) }
      if (url.endsWith('/confirmations/previews')) return envelope({ resource_ref: { id: jobId, type: 'job' }, action: 'job.retry', revision: 1, config_hash: 'a'.repeat(64), etag: '"job-etag-v1"', summary: [{ label: '动作', value: '重试导出任务' }], dependencies: [], impact: {}, can_confirm: true, blockers: [], policy_version: 'v1' })
      if (url.endsWith('/confirmations') && method === 'POST') return envelope({ id: 'confirmation-job-retry', project_id: 'project-export', actor_id: 'actor-private', resource_ref: { id: jobId, type: 'job' }, action: 'job.retry', revision: 1, config_hash: 'a'.repeat(64), policy_version: 'v1', status: 'CONFIRMED', created_at: '', expires_at: '', consumed_at: null, consumed_result_ref: null, summary_snapshot: {} }, 201, { ETag: '"confirmation-v1"' })
      if (url.endsWith('/artifacts/artifact-export-1')) {
        if (artifactReadFailure) return new Response(JSON.stringify({ error: { code: 'SECRET_ERROR', message: 'secret internal detail' } }), { status: artifactReadFailure, headers: { 'content-type': 'application/json' } })
        return envelope({ id: 'artifact-export-1', project_id: 'project-export', owner_id: 'actor-private', kind: 'export_report', file_name: 'records.csv', media_type: 'text/csv', size_bytes: 12, sha256: null, status: artifactStatus, scan_status: 'PASSED', created_at: '', updated_at: '', download_allowed: artifactStatus === 'AVAILABLE' && artifactDownloadAllowed })
      }
      if (url.endsWith('/artifacts/artifact-export-1/download')) {
        if (artifactDownloadFailure) return new Response(JSON.stringify({ error: { code: 'SECRET_ERROR', message: 'secret internal detail' } }), { status: artifactDownloadFailure, headers: { 'content-type': 'application/json' } })
        return new Response(new Blob(['record_id\nrecord-export-1']), { status: 200, headers: { 'content-type': 'text/csv' } })
      }
      throw new Error(`Unexpected ${method} ${url}`)
    }))
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

  async function openPage(settle = true) {
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/data', name: 'data', component: PredictionRecordsView }] })
    await router.push('/data'); await router.isReady()
    const wrapper = mount(PredictionRecordsView, { global: { plugins: [createPinia(), router], stubs: { RouterLink: true } } })
    if (settle) await flushPromises()
    else await nextTick()
    return wrapper
  }

  async function submitCsv(wrapper: ReturnType<typeof mount>) {
    await wrapper.get('input[aria-label="选择记录 record-export-1 用于导出"]').setValue(true)
    await wrapper.get('select[aria-label="导出格式"]').setValue('csv')
    await wrapper.get('[data-testid="create-record-export"]').trigger('click')
    await flushPromises()
  }

  it.each(['resource_ref', 'location', 'status', 'missing_location', 'external_location', 'prefix_location', 'query_location', 'hash_location'] as const)('does not treat malformed formal acceptance (%s) as export success', async (fault) => {
    exportAcceptanceFault = fault
    const wrapper = await openPage()
    await submitCsv(wrapper)
    const store = useRecordExportStore()
    expect(store.accepted).toBeNull()
    expect(store.exportView).toBeNull()
    expect(store.state).toBe('error')
    expect(requests.some((request) => request.url === `/api/v1/exports/${exportId}`)).toBe(false)
    expect(wrapper.get('[data-testid="record-export-state-error"]').text()).toContain('受理状态未知')
    wrapper.unmount()
  })

  it.each(['same_origin_absolute', 'path_relative_location'] as const)('accepts a legal %s URI-reference for the created export', async (fault) => {
    exportAcceptanceFault = fault
    const wrapper = await openPage()
    await submitCsv(wrapper)
    const store = useRecordExportStore()
    expect(store.accepted).toMatchObject({ id: exportId, resource_ref: { type: 'export', id: exportId } })
    expect(store.state).toBe('ready')
    wrapper.unmount()
  })

  it('does not use an export view that contains fields outside the frozen DTO', async () => {
    exportAcceptanceFault = 'view'
    sessionStorage.setItem('chrom-platform.record-export:project-export', JSON.stringify({ exportId }))
    const wrapper = await openPage()
    const store = useRecordExportStore()
    expect(store.exportView).toBeNull()
    expect(store.state).toBe('error')
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(false)
  })

  it('keeps an empty records state honest and disables export without resource refs', async () => {
    emptyRecordList = true
    const wrapper = await openPage()
    expect(wrapper.text()).toContain('服务端没有返回记录')
    expect(wrapper.find('[data-testid="record-export"]').exists()).toBe(true)
    expect((wrapper.get('[data-testid="create-record-export"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(requests.some((request) => request.url === '/api/v1/exports' && request.method === 'POST')).toBe(false)
  })

  it('shows unavailable while capabilities load, then exposes only advertised contract formats', async () => {
    let resolveCapabilities!: (response: Response) => void
    capabilityGate = new Promise<Response>((resolve) => { resolveCapabilities = resolve })
    const wrapper = await openPage(false)
    expect(wrapper.get('[data-testid="record-export-capability"]').text()).toContain('正在读取服务端导出能力')
    expect((wrapper.get('select[aria-label="导出格式"]').element as HTMLSelectElement).disabled).toBe(true)
    expect((wrapper.get('[data-testid="create-record-export"]').element as HTMLButtonElement).disabled).toBe(true)

    capabilityGate = null
    resolveCapabilities(envelope({ build_id: 'universal-v1', api_version: '1.0', modules: [], chromatography_types: [], assistant_modes: [], export_formats: ['csv', 'unknown', 'csv'], auth_mode: 'deployment_default', data_origin: 'production', capabilities: [] }))
    await flushPromises()
    const select = wrapper.get('select[aria-label="导出格式"]')
    expect(select.findAll('option').map((option) => (option.element as HTMLOptionElement).value)).toEqual(['csv'])
    expect((select.element as HTMLSelectElement).disabled).toBe(false)
    await wrapper.get('input[aria-label="选择记录 record-export-1 用于导出"]').setValue(true)
    await wrapper.get('[data-testid="create-record-export"]').trigger('click')
    await flushPromises()
    expect(requests.find((request) => request.url === '/api/v1/exports' && request.method === 'POST')?.body).toMatchObject({ format: 'csv' })
  })

  it('disables export when the server advertises no formats and sends no request', async () => {
    exportFormats = []
    const wrapper = await openPage()
    expect(wrapper.get('[data-testid="record-export-capability"]').text()).toContain('服务端当前未声明合同支持的导出格式')
    expect(wrapper.findAll('select[aria-label="导出格式"] option').map((option) => option.text())).toEqual(['暂不可用'])
    expect((wrapper.get('select[aria-label="导出格式"]').element as HTMLSelectElement).disabled).toBe(true)
    await wrapper.get('input[aria-label="选择记录 record-export-1 用于导出"]').setValue(true)
    expect((wrapper.get('[data-testid="create-record-export"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(requests.some((request) => request.url === '/api/v1/exports' && request.method === 'POST')).toBe(false)
  })

  it('blocks a previously selected format if the server revokes export capability at runtime', async () => {
    exportFormats = ['csv']
    const wrapper = await openPage()
    expect(wrapper.get('select[aria-label="导出格式"]').element).toHaveProperty('value', 'csv')
    await wrapper.get('input[aria-label="选择记录 record-export-1 用于导出"]').setValue(true)
    exportFormats = []
    await useAppStore().load()
    await flushPromises()
    expect(wrapper.get('[data-testid="record-export-capability"]').text()).toContain('服务端当前未声明合同支持的导出格式')
    expect((wrapper.get('[data-testid="create-record-export"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('[data-testid="create-record-export"]').trigger('click')
    expect(requests.some((request) => request.url === '/api/v1/exports' && request.method === 'POST')).toBe(false)
  })

  it('submits only selected record refs using the frozen format/include fields and sends guarded cancellation', async () => {
    const wrapper = await openPage()
    expect((wrapper.get('[data-testid="create-record-export"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.text()).toContain('JSON'); expect(wrapper.text()).toContain('CSV'); expect(wrapper.text()).toContain('XLSX')
    await wrapper.get('input[aria-label="选择记录 record-export-1 用于导出"]').setValue(true)
    await wrapper.get('input[aria-label="选择记录 record-export-2 用于导出"]').setValue(true)
    await wrapper.get('select[aria-label="导出格式"]').setValue('csv')
    const createButton = wrapper.get('[data-testid="create-record-export"]').element
    createButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    createButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    const create = requests.find((request) => request.url === '/api/v1/exports' && request.method === 'POST')!
    expect(create.body).toEqual({ project_id: 'project-export', resource_refs: [{ type: 'prediction_record', id: record.id }, { type: 'prediction_record', id: record2.id }], format: 'csv', include: ['input_snapshot', 'result', 'model_version', 'warnings'] })
    expect(requests.filter((request) => request.url === '/api/v1/exports' && request.method === 'POST')).toHaveLength(1)
    expect(create.headers.get('Idempotency-Key')).toBeTruthy()
    expect(wrapper.get('[data-testid="record-export-status"]').text()).toContain('RUNNING')
    await wrapper.findAll('[data-testid="record-export-status"] button').find((button) => button.text().includes('取消导出'))!.trigger('click')
    await flushPromises()
    const cancel = requests.find((request) => request.url.endsWith(`/jobs/${jobId}/cancel`))!
    expect(cancel.headers.get('If-Match')).toBe('"job-etag-v1"')
    expect(cancel.headers.get('Idempotency-Key')).toBeTruthy()
    expect(cancel.body).toEqual({ reason: null })
    expect(wrapper.text()).toContain('CANCEL_REQUESTED')
  })

  it('restores the export after refresh and downloads only an available export_report artifact', async () => {
    const first = await openPage(); await submitCsv(first); first.unmount()
    exportStatus = 'SUCCEEDED'; jobStatus = 'SUCCEEDED'; cancelable = false
    const createObjectURL = vi.fn(() => 'blob:export-file'); const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const refreshed = await openPage()
    await flushPromises()
    expect(requests.filter((request) => request.url.endsWith(`/exports/${exportId}`)).length).toBeGreaterThanOrEqual(2)
    expect(refreshed.get('[data-testid="record-export-status"]').text()).toContain('SUCCEEDED')
    expect(refreshed.get('[data-testid="download-record-export"]').text()).toContain('records.csv')
    await refreshed.get('[data-testid="download-record-export"]').trigger('click'); await flushPromises()
    expect(requests.some((request) => request.url.endsWith('/artifacts/artifact-export-1'))).toBe(true)
    expect(requests.some((request) => request.url.endsWith('/artifacts/artifact-export-1/download'))).toBe(true)
    expect(createObjectURL).toHaveBeenCalledOnce(); expect(anchorClick).toHaveBeenCalledOnce(); expect(revokeObjectURL).toHaveBeenCalledWith('blob:export-file')
  })

  it.each([
    ['export GET', 403, null], ['export GET', 404, null],
    ['job GET', null, 403], ['job GET', null, 404],
  ] as const)('fails closed when restored %s returns %s/%s', async (_endpoint, exportFailure, jobFailure) => {
    exportStatus = 'SUCCEEDED'; jobStatus = 'FAILED'; retryable = true
    exportReadFailure = exportFailure; jobReadFailure = jobFailure
    sessionStorage.setItem('chrom-platform.record-export:project-export', JSON.stringify({ exportId }))
    const wrapper = await openPage()
    const store = useRecordExportStore()
    expect(wrapper.get('[data-testid="record-export-state-error"]').text()).toContain('不可读取')
    expect(wrapper.text()).not.toContain('secret internal detail')
    expect(wrapper.find('[data-testid="record-export-status"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="record-export-reload"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(false)
    expect(store.accessDenied).toBe(true)
    expect(store.job).toBeNull(); expect(store.artifact).toBeNull(); expect(store.exportView).toBeNull()
    await store.cancel(); await store.retry('unused-confirmation')
    expect(requests.some((request) => request.method === 'POST' && (/\/cancel$|\/retry$/).test(request.url))).toBe(false)
  })

  it.each([403, 404])('clears stale artifact and job actions when artifact metadata returns %s', async (status) => {
    exportStatus = 'SUCCEEDED'; jobStatus = 'FAILED'; retryable = true
    artifactReadFailure = status
    sessionStorage.setItem('chrom-platform.record-export:project-export', JSON.stringify({ exportId }))
    const wrapper = await openPage()
    const store = useRecordExportStore()
    expect(wrapper.get('[data-testid="record-export-state-error"]').text()).toContain('不可读取')
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(false)
    expect(store.accessDenied).toBe(true); expect(store.job).toBeNull(); expect(store.artifact).toBeNull()
    expect(wrapper.text()).not.toContain('secret internal detail')
  })

  it('blocks the old download after a 403 and requires a successful reread before restoring actions', async () => {
    exportStatus = 'SUCCEEDED'; jobStatus = 'FAILED'; retryable = true
    sessionStorage.setItem('chrom-platform.record-export:project-export', JSON.stringify({ exportId }))
    const wrapper = await openPage()
    const store = useRecordExportStore()
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(true)
    artifactDownloadFailure = 403
    await wrapper.get('[data-testid="download-record-export"]').trigger('click'); await flushPromises()
    expect(wrapper.get('[data-testid="record-export-state-error"]').text()).toContain('当前导出或制品不可下载')
    expect(wrapper.text()).not.toContain('secret internal detail')
    expect(wrapper.find('[data-testid="record-export-status"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(false)
    expect(store.accessDenied).toBe(true); expect(store.artifact).toBeNull(); expect(store.job).toBeNull()
    artifactDownloadFailure = null
    await wrapper.get('[data-testid="record-export-reload"]').trigger('click'); await flushPromises()
    expect(store.state).toBe('ready'); expect(store.accessDenied).toBe(false)
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(true)
  })

  it('keeps cached authorization metadata on transient read failures but requires a successful reread for actions', async () => {
    exportStatus = 'SUCCEEDED'; jobStatus = 'FAILED'; retryable = true
    sessionStorage.setItem('chrom-platform.record-export:project-export', JSON.stringify({ exportId }))
    const wrapper = await openPage()
    const store = useRecordExportStore()
    const oldArtifact = store.artifact; const oldJob = store.job
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="record-export-status"] button').some((button) => button.text().includes('重试'))).toBe(true)

    exportReadFailure = 503
    await wrapper.findAll('[data-testid="record-export-status"] button').find((button) => button.text().includes('刷新导出状态'))!.trigger('click')
    await flushPromises()
    expect(store.state).toBe('error'); expect(store.accessDenied).toBe(false)
    expect(store.artifact).toBe(oldArtifact); expect(store.job).toBe(oldJob)
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="record-export-status"] button').some((button) => button.text().includes('重试'))).toBe(false)
    expect(wrapper.text()).not.toContain('secret internal detail')

    exportReadFailure = null
    await wrapper.findAll('[data-testid="record-export-status"] button').find((button) => button.text().includes('刷新导出状态'))!.trigger('click')
    await flushPromises()
    expect(store.state).toBe('ready'); expect(store.artifact).toEqual(oldArtifact); expect(store.job).toEqual(oldJob)
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(true)

    exportReadNetworkFailure = true
    await wrapper.findAll('[data-testid="record-export-status"] button').find((button) => button.text().includes('刷新导出状态'))!.trigger('click')
    await flushPromises()
    expect(store.state).toBe('error'); expect(store.accessDenied).toBe(false)
    expect(store.artifact).toEqual(oldArtifact); expect(store.job).toEqual(oldJob)
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('secret transport detail')

    exportReadNetworkFailure = false
    await wrapper.findAll('[data-testid="record-export-status"] button').find((button) => button.text().includes('刷新导出状态'))!.trigger('click')
    await flushPromises()
    expect(store.state).toBe('ready'); expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(true)
  })

  it('blocks cancel and retry when SUCCEEDED contradicts the server action flags', async () => {
    exportStatus = 'SUCCEEDED'; jobStatus = 'SUCCEEDED'; cancelable = true; retryable = true
    sessionStorage.setItem('chrom-platform.record-export:project-export', JSON.stringify({ exportId }))
    const wrapper = await openPage()
    const store = useRecordExportStore()
    expect(store.canCancel).toBe(false)
    expect(store.canRetry).toBe(false)
    expect(wrapper.findAll('[data-testid="record-export-status"] button').some((button) => button.text().includes('取消导出'))).toBe(false)
    expect(wrapper.findAll('[data-testid="record-export-status"] button').some((button) => button.text().includes('重试'))).toBe(false)

    await store.cancel()
    await store.retry('confirmation-unused')
    expect(requests.some((request) => request.url.endsWith(`/jobs/${jobId}/cancel`) && request.method === 'POST')).toBe(false)
    expect(requests.some((request) => request.url.endsWith(`/jobs/${jobId}/retry`) && request.method === 'POST')).toBe(false)
  })

  it('reuses the pending idempotency key after an uncertain response and keeps project recovery isolated', async () => {
    failExportPost = true
    const wrapper = await openPage(); await submitCsv(wrapper)
    expect(wrapper.text()).toContain('受理状态未知')
    expect(wrapper.text()).not.toContain('secret internal detail')
    const firstKey = requests.find((request) => request.url === '/api/v1/exports' && request.method === 'POST')!.headers.get('Idempotency-Key')
    await wrapper.get('[data-testid="create-record-export"]').trigger('click'); await flushPromises()
    const createRequests = requests.filter((request) => request.url === '/api/v1/exports' && request.method === 'POST')
    expect(createRequests).toHaveLength(2)
    expect(createRequests[1].headers.get('Idempotency-Key')).toBe(firstKey)

    setActivePinia(createPinia())
    const store = useRecordExportStore()
    await store.restore('project-other')
    await store.restore('project-export')
    expect(sessionStorage.getItem('chrom-platform.record-export:project-other')).toBeNull()
    expect(sessionStorage.getItem('chrom-platform.record-export:project-export')).toContain(exportId)
  })

  it('keeps a pending submit isolated by project until the same request is acknowledged', async () => {
    failExportPost = true
    setActivePinia(createPinia())
    const store = useRecordExportStore()
    const body: ContractCreateRecordExportBody = { project_id: 'project-export', resource_refs: [{ type: 'prediction_record', id: record.id }], format: 'csv', include: ['input_snapshot', 'result', 'model_version', 'warnings'] }
    await store.create(body)
    expect(store.pendingSubmission?.body.resource_refs).toEqual([{ type: 'prediction_record', id: record.id }])
    await store.restore('project-other')
    await store.restore('project-export')
    expect(store.pendingSubmission?.body.resource_refs).toEqual([{ type: 'prediction_record', id: record.id }])
    expect(sessionStorage.getItem('chrom-platform.record-export:project-other')).toBeNull()
    expect(sessionStorage.getItem('chrom-platform.record-export:project-export')).toContain('idempotencyKey')
  })

  it('does not let a late project A acceptance overwrite project B state', async () => {
    setActivePinia(createPinia())
    const store = useRecordExportStore()
    const body: ContractCreateRecordExportBody = { project_id: 'project-export', resource_refs: [{ type: 'prediction_record', id: record.id }], format: 'csv', include: ['result'] }
    const normalFetch = vi.mocked(globalThis.fetch)
    let accept!: (response: Response) => void
    const delayed = new Promise<Response>((resolve) => { accept = resolve })
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => String(input) === '/api/v1/exports' ? delayed : normalFetch(input, init))
    const creating = store.create(body)
    await Promise.resolve()
    await store.restore('project-other')
    accept(envelope({ id: exportId, job_id: jobId, status: 'QUEUED', resource_ref: { type: 'export', id: exportId } }, 202, { Location: `/api/v1/exports/${exportId}` }))
    await creating
    expect(store.projectId).toBe('project-other')
    expect(store.exportId).toBeNull()
    expect(store.accepted).toBeNull()
    expect(sessionStorage.getItem('chrom-platform.record-export:project-export')).toContain(exportId)
    await store.restore('project-export')
    expect(store.exportView?.id).toBe(exportId)
  })

  it('does not expose a download when artifact metadata denies it', async () => {
    exportStatus = 'SUCCEEDED'; jobStatus = 'SUCCEEDED'; cancelable = false; artifactDownloadAllowed = false
    sessionStorage.setItem('chrom-platform.record-export:project-export', JSON.stringify({ exportId }))
    const wrapper = await openPage()
    expect(wrapper.get('[data-testid="record-export-status"]').text()).toContain('AVAILABLE')
    expect(wrapper.text()).toContain('服务端当前不允许下载')
    expect(wrapper.find('[data-testid="download-record-export"]').exists()).toBe(false)
    expect(requests.some((request) => request.url.endsWith('/artifacts/artifact-export-1/download'))).toBe(false)
  })

  it('requires the job.retry confirmation and sends it with current ETag for a server-retryable job', async () => {
    exportStatus = 'FAILED'; jobStatus = 'FAILED'; retryable = true; cancelable = false
    const wrapper = await openPage(); await submitCsv(wrapper)
    await wrapper.get('[data-testid="record-export-status"] button:last-of-type').trigger('click')
    await flushPromises()
    await wrapper.get('.confirmation-card button').trigger('click'); await flushPromises()
    await wrapper.get('.confirmation-card button.primary').trigger('click'); await flushPromises()
    expect(requests.some((request) => request.url.endsWith('/confirmations') && request.method === 'POST')).toBe(true)
    await wrapper.findAll('[data-testid="record-export-status"] button').find((button) => button.text().includes('确认后重试'))!.trigger('click'); await flushPromises()
    const retry = requests.find((request) => request.url.endsWith(`/jobs/${jobId}/retry`))!
    expect(retry.headers.get('If-Match')).toBe('"job-etag-v1"')
    expect(retry.headers.get('Idempotency-Key')).toBeTruthy()
    expect(retry.body).toEqual({ confirmation_id: 'confirmation-job-retry' })
  })
})
