import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TlcBatchWorkspace from '../candidates/tlc-batch-revision20261008-01/src/modules/prediction/forms/tlc/TlcBatchWorkspace.vue'
import { createTlcBatchClient, type TlcBatchToolCapability, type TlcBatchTypeCapability } from '../candidates/tlc-batch-revision20261008-01/src/modules/prediction/forms/tlc/tlc-batch-client'

vi.mock('@/components/JobStatus.vue', () => ({ default: { props: ['jobId'], template: '<div data-testid="job-status">{{ jobId }}</div>' } }))

const opaqueCursor = `${'A'.repeat(980)}.${'S'.repeat(43)}`

function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, request_id: 'qa-fixture', warnings: [] }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function pageError() {
  return new Response(JSON.stringify({ error: { code: 'VALIDATION_FAILED', message: 'cursor rejected by fixture' } }), {
    status: 400,
    headers: { 'content-type': 'application/json' },
  })
}

function typeCapability(): TlcBatchTypeCapability {
  return {
    id: 'tlc', display_name: 'TLC', health: { status: 'READY', checked_at: '2026-10-08T00:00:00Z', reason: null },
    limits: { max_rows: 100, max_candidates: 10, max_evaluations: 100 },
    models: [{ id: 'model-server-id', version: 'v1.1', input_schema_version: '1.1', output_schema_version: '1.1', scope: 'TLC model', task_type: 'tlc', health: { status: 'READY', checked_at: '2026-10-08T00:00:00Z', reason: null } }],
    tools: [toolCapability()],
  }
}

function toolCapability(): TlcBatchToolCapability {
  return {
    id: 'batch', chromatography_type: 'tlc', schema_version: '1.1', template_version: 'tlc-batch-2',
    health: { status: 'READY', checked_at: '2026-10-08T00:00:00Z', reason: null },
    limits: { max_runtime_seconds: 3600, max_evaluations: 100 },
    input_schema: { type: 'object', properties: {} }, output_schema: { type: 'object', properties: {} },
    model_constraints: {
      template_versions: ['tlc-batch-2'],
      compatible_models: [{ model_id: 'model-server-id', model_version: 'v1.1', input_schema_version: '1.1', template_versions: ['tlc-batch-2'], health: { status: 'READY', checked_at: '2026-10-08T00:00:00Z', reason: null } }],
    },
    structure_count: { min: 1, max: 1, semantics: 'batch' }, supported_visualizations: [], units: {},
    templates: [{ id: 'tlc_batch', chromatography_type: 'tlc', tool: 'batch', version: 'tlc-batch-2',
      row_schema: 'TlcBatchRowV2', fields: ['smiles', 'solvent_1', 'fraction_1', 'solvent_2', 'fraction_2'],
      limits: { max_runtime_seconds: 3600, max_evaluations: 100 }, health: { status: 'READY', checked_at: '2026-10-08T00:00:00Z', reason: null },
      tlc_options: { solvent_options: [{ id: 'hexane' }, { id: 'ethyl_acetate' }], allowed_systems: [{ id: 'hexane-ethyl_acetate', solvent_ids: ['hexane', 'ethyl_acetate'] }] } }],
  }
}

function recoveryDraft(overrides: Record<string, unknown> = {}) {
  return {
    id: 'batch-server', project_id: 'project-from-context', chromatography_type: 'tlc', tool: 'batch', config_hash: 'b'.repeat(64),
    draft_status: 'READY', execution_status: null, job_id: null,
    counts: { total: 2, queued: 0, running: 0, succeeded: 0, failed: 0, skipped: 0, canceled: 0 }, progress: 0,
    revision: 1, etag: '"batch-etag-r1"', name: 'TLC batch', schema_version: '1.1',
    model_selection: { mode: 'exact', model_id: 'model-server-id', model_version: 'v1.1' }, input_artifact_id: 'artifact-server-issued',
    template_version: 'tlc-batch-2', field_mapping: { smiles: 'smiles', solvent_1: 'solvent_1', fraction_1: 'fraction_1', solvent_2: 'solvent_2', fraction_2: 'fraction_2' },
    common_input: {}, invalid_row_policy: 'exclude_invalid', resource_budget: { max_evaluations: 100, max_runtime_seconds: 3600 }, validation_id: 'validation-1',
    ...overrides,
  }
}

function recoveryValidation(status: 'READY' | 'RUNNING' = 'READY') {
  return {
    id: 'validation-1', draft_revision: 1, config_hash: 'b'.repeat(64), status, template_version: 'tlc-batch-2',
    model_snapshot: { id: 'model-server-id', version: 'v1.1', input_schema_version: '1.1', output_schema_version: '1.1', artifact_sha256: 'e'.repeat(64), business_scope_id: 'scope-tlc' },
    total_rows: 2, valid_rows: 2, invalid_rows: 0, excluded_rows: 0, total_evaluations: 2,
    resource_estimate: { max_evaluations: 100, max_runtime_seconds: 3600 }, resolved_input_hash: 'd'.repeat(64),
  }
}

function artifactView() {
  return { id: 'artifact-server-issued', project_id: 'project-from-context', kind: 'batch_input', status: 'AVAILABLE', scan_status: 'PASSED', file_name: 'input.csv', media_type: 'text/csv', size_bytes: 42, owner_id: 'actor', created_at: '2026-10-08T00:00:00Z', updated_at: '2026-10-08T00:00:01Z', download_allowed: false, sha256: null }
}

function validationRow(row: number) {
  return { row_id: `validation-row-${row}`, source_row_number: row + 1, status: 'VALID', normalized_input_hash: String(row).repeat(64), resolved_input_hash: String(row + 1).repeat(64), resolved_input_snapshot: { chromatography_type: 'tlc', template_version: 'tlc-batch-2', project_id: 'project-from-context', draft_revision: 1, validation_id: 'validation-1', structure_ref: `structure-${row}`, normalized_smiles: 'CCO', solvent_conditions: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.7 }, { solvent_id: 'ethyl_acetate', fraction: 0.3 }] } }, warnings: [] }
}

function resultRow(row: number) {
  return { row_id: `result-row-${row}`, attempt: 1, source_row_number: row + 1, status: 'FAILED', input_snapshot: { chromatography_type: 'tlc', template_version: 'tlc-batch-2', project_id: 'project-from-context', draft_revision: 1, validation_id: 'validation-1', structure_ref: `structure-${row}`, normalized_smiles: 'CCO', solvent_conditions: { solvent_components: [{ solvent_id: 'hexane', fraction: 0.7 }, { solvent_id: 'ethyl_acetate', fraction: 0.3 }] } }, result: null, error: { code: 'MODEL_ERROR', message: 'fixture row failure' }, warnings: [] }
}

function capture(input: RequestInfo | URL, init?: RequestInit) {
  return { url: String(input), method: init?.method ?? 'GET' }
}

beforeEach(() => { setActivePinia(createPinia()); sessionStorage.clear() })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('signed cursor frontend compatibility QA', () => {
  it('passes an opaque 1024-character cursor through URLSearchParams for both row endpoints', async () => {
    expect(opaqueCursor).toHaveLength(1024)
    const requests: ReturnType<typeof capture>[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(capture(input, init))
      return envelope({ rows: [], has_more: false, next_cursor: opaqueCursor })
    }))
    const client = createTlcBatchClient()

    const validation = await client.listValidationRows('batch-server', { cursor: opaqueCursor, limit: 200 })
    const results = await client.listRows('batch-server', { cursor: opaqueCursor, limit: 200 })
    const validationUrl = new URL(requests[0]!.url, 'http://localhost')
    const resultsUrl = new URL(requests[1]!.url, 'http://localhost')

    expect(validationUrl.pathname).toBe('/api/v1/batch-predictions/batch-server/validation/rows')
    expect(resultsUrl.pathname).toBe('/api/v1/batch-predictions/batch-server/rows')
    expect(validationUrl.searchParams.get('cursor')).toBe(opaqueCursor)
    expect(resultsUrl.searchParams.get('cursor')).toBe(opaqueCursor)
    expect(validation.next_cursor).toBe(opaqueCursor)
    expect(results.next_cursor).toBe(opaqueCursor)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
  })

  it('recovers validation preview from the server pointer and retries page one after a typed cursor error', async () => {
    const requests: ReturnType<typeof capture>[] = []
    let rowRequests = 0
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = capture(input, init); requests.push(request)
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname.endsWith('/prediction/types/tlc')) return envelope(typeCapability())
      if (url.pathname.endsWith('/prediction/types/tlc/tools/batch/schema')) return envelope(toolCapability())
      if (url.pathname.endsWith('/batch-predictions/batch-server')) return envelope(recoveryDraft())
      if (url.pathname.endsWith('/artifacts/artifact-server-issued')) return envelope(artifactView())
      if (url.pathname.endsWith('/batch-predictions/batch-server/validation')) return envelope(recoveryValidation())
      if (url.pathname.endsWith('/batch-predictions/batch-server/validation/rows')) {
        rowRequests += 1
        const cursor = url.searchParams.get('cursor')
        if (rowRequests === 1 || rowRequests === 3) {
          expect(cursor).toBeNull()
          return envelope({ rows: [validationRow(1)], has_more: true, next_cursor: opaqueCursor })
        }
        expect(cursor).toBe(opaqueCursor)
        if (rowRequests === 2) return pageError()
        return envelope({ rows: [validationRow(2)], has_more: false, next_cursor: null })
      }
      throw new Error(`Unexpected QA request: ${request.method} ${request.url}`)
    }))
    sessionStorage.setItem('tlc-batch-candidate.active.v1', JSON.stringify({ project_id: 'project-from-context', batch_id: 'batch-server' }))
    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true, canCreateBatch: true } })
    await flushPromises()
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    await wrapper.get('[data-testid="restore-batch-draft"]').trigger('click')
    await flushPromises()

    expect(rowRequests).toBe(2)
    expect(wrapper.get('[data-testid="typed-preview-error"]').text()).toContain('cursor rejected by fixture')
    await wrapper.get('[data-testid="refresh-batch-preview"]').trigger('click')
    await flushPromises()

    expect(rowRequests).toBe(4)
    expect(requests.filter((request) => request.url.includes('/validation/rows')).map((request) => new URL(request.url, 'http://localhost').searchParams.get('cursor'))).toEqual([null, opaqueCursor, null, opaqueCursor])
    expect(wrapper.find('[data-testid="typed-preview-error"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="typed-preview"] .preview-row')).toHaveLength(2)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it('recovers result rows from the server pointer and manually restarts after a cursor error', async () => {
    const requests: ReturnType<typeof capture>[] = []
    let rowRequests = 0
    const currentDraft = recoveryDraft({ job_id: 'job-server', execution_status: 'RUNNING' })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = capture(input, init); requests.push(request)
      const url = new URL(request.url, 'http://localhost')
      if (url.pathname.endsWith('/prediction/types/tlc')) return envelope(typeCapability())
      if (url.pathname.endsWith('/prediction/types/tlc/tools/batch/schema')) return envelope(toolCapability())
      if (url.pathname.endsWith('/batch-predictions/batch-server')) return envelope(currentDraft)
      if (url.pathname.endsWith('/artifacts/artifact-server-issued')) return envelope(artifactView())
      if (url.pathname.endsWith('/batch-predictions/batch-server/validation')) return envelope(recoveryValidation('RUNNING'))
      if (url.pathname.endsWith('/batch-predictions/batch-server/rows')) {
        rowRequests += 1
        const cursor = url.searchParams.get('cursor')
        if (rowRequests === 1 || rowRequests === 3) {
          expect(cursor).toBeNull()
          return envelope({ rows: [resultRow(1)], has_more: true, next_cursor: opaqueCursor })
        }
        expect(cursor).toBe(opaqueCursor)
        if (rowRequests === 2) return pageError()
        return envelope({ rows: [resultRow(2)], has_more: false, next_cursor: null })
      }
      throw new Error(`Unexpected QA request: ${request.method} ${request.url}`)
    }))
    sessionStorage.setItem('tlc-batch-candidate.active.v1', JSON.stringify({ project_id: 'project-from-context', batch_id: 'batch-server' }))
    const wrapper = mount(TlcBatchWorkspace, { props: { projectId: 'project-from-context', canReadBatch: true, canUploadBatch: true, canCreateBatch: true } })
    await flushPromises()
    await wrapper.get('[data-testid="batch-model-select"]').setValue('model-server-id\u0000v1.1\u00001.1')
    await flushPromises()
    await wrapper.get('[data-testid="restore-batch-draft"]').trigger('click')
    await flushPromises()

    expect(rowRequests).toBe(2)
    expect(wrapper.get('[data-testid="batch-operation-error"]').text()).toContain('cursor rejected by fixture')
    await wrapper.get('[data-testid="refresh-batch-results"]').trigger('click')
    await flushPromises()

    expect(rowRequests).toBe(4)
    expect(requests.filter((request) => new URL(request.url, 'http://localhost').pathname.endsWith('/batch-predictions/batch-server/rows')).map((request) => new URL(request.url, 'http://localhost').searchParams.get('cursor'))).toEqual([null, opaqueCursor, null, opaqueCursor])
    expect(wrapper.find('[data-testid="batch-operation-error"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-testid="batch-result-rows"] .result-row')).toHaveLength(2)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
  })
})
