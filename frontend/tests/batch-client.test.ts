import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createBatchClient, type BatchClientScope } from '../src/shared/batch/batch-client'
import type { BatchDraftCreate } from '../src/shared/batch/batch-types'

const scope: BatchClientScope = { actorId: 'actor-a', projectId: 'project-a', type: 'gc', tool: 'batch' }
const draftBody: BatchDraftCreate = {
  project_id: 'project-a',
  name: 'GC batch',
  schema_version: '1.1',
  model_selection: { mode: 'champion', scope_id: 'scope-gc' },
  input_artifact_id: 'artifact-1',
  template_version: 'gc-batch-1',
  field_mapping: { smiles: 'SMILES' },
  invalid_row_policy: 'block',
  resource_budget: { max_runtime_seconds: 600, max_evaluations: 100 },
  chromatography_type: 'gc',
  tool: 'batch',
}

function jsonResponse(data: unknown, status = 200, etag?: string) {
  return new Response(JSON.stringify({ data, request_id: 'request-test', warnings: [] }), {
    status,
    headers: { 'content-type': 'application/json', ...(etag ? { ETag: etag } : {}) },
  })
}

function capture(input: RequestInfo | URL, init?: RequestInit) {
  return { url: String(input), method: init?.method ?? 'GET', headers: new Headers(init?.headers), body: init?.body }
}

beforeEach(() => vi.restoreAllMocks())

describe('shared batch client', () => {
  it('keeps TLC batch, non-TLC batch, and non-batch tool capability branches distinct', async () => {
    const responses = [
      { id: 'batch', chromatography_type: 'tlc', templates: [], model_constraints: { compatible_models: [], template_versions: [] } },
      { id: 'batch', chromatography_type: 'gc', templates: [], model_constraints: { compatible_models: [], template_versions: [] } },
      { id: 'single' },
      { id: 'batch', chromatography_type: 'cc', templates: [], model_constraints: { compatible_models: [], template_versions: [] } },
    ]
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(responses.shift())))

    const tlc = await createBatchClient({ ...scope, type: 'tlc' }).getToolCapability('1.0')
    const nonTlc = await createBatchClient(scope).getToolCapability('1.0')
    const nonBatch = await createBatchClient({ ...scope, type: 'cc' }).getToolCapability('1.0')
    expect('chromatography_type' in tlc && tlc.chromatography_type).toBe('tlc')
    expect('chromatography_type' in nonTlc && nonTlc.chromatography_type).toBe('gc')
    expect(nonBatch.id).toBe('single')
    await expect(createBatchClient({ ...scope, type: 'tlc' }).getToolCapability('1.0')).rejects.toThrow('不匹配')
  })

  it('binds paths to type/tool and reuses scoped idempotency keys for concurrent writes', async () => {
    const requests: ReturnType<typeof capture>[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(capture(input, init))
      await Promise.resolve()
      return jsonResponse({ id: 'draft-1' }, 201, 'etag-1')
    }))
    const client = createBatchClient(scope)

    const [first, second] = await Promise.all([client.createDraft(draftBody), client.createDraft(draftBody)])
    await createBatchClient({ ...scope, actorId: 'actor-b' }).createDraft(draftBody)

    expect(first.data).toEqual(second.data)
    expect(requests.map((request) => request.url)).toEqual([
      '/api/v1/batch-predictions/drafts',
      '/api/v1/batch-predictions/drafts',
    ])
    expect(requests[0]!.headers.get('Idempotency-Key')).not.toBe(requests[1]!.headers.get('Idempotency-Key'))
    expect(requests).toHaveLength(2)
  })

  it('uses ETag for draft mutation and supports retry-plan and confirmed retry routes', async () => {
    const requests: ReturnType<typeof capture>[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(capture(input, init))
      return jsonResponse({ id: 'resource-1' }, 202)
    }))
    const client = createBatchClient(scope)

    await client.patchDraft('draft / one', { name: 'updated' }, 'etag-r2')
    await client.createRetryPlan('batch-1', ['row-1'], 'etag-r4')
    await client.retryFailed('batch-1', 'plan-1', 'confirm-1', 'etag-r4')

    expect(requests.map(({ url, method }) => [url, method])).toEqual([
      ['/api/v1/batch-predictions/draft%20%2F%20one', 'PATCH'],
      ['/api/v1/batch-predictions/batch-1/retry-plans', 'POST'],
      ['/api/v1/batch-predictions/batch-1/retry-failed', 'POST'],
    ])
    expect(requests.map((request) => request.headers.get('If-Match'))).toEqual(['etag-r2', 'etag-r4', 'etag-r4'])
    expect(JSON.parse(requests[1]!.body as string)).toEqual({ row_ids: ['row-1'] })
    expect(JSON.parse(requests[2]!.body as string)).toEqual({ retry_plan_id: 'plan-1', confirmation_id: 'confirm-1' })
  })

  it('creates exports, reads their status, and downloads without resubmitting a batch', async () => {
    const requests: ReturnType<typeof capture>[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(capture(input, init))
      if (String(input).endsWith('/download')) return new Response('csv-data', { status: 200, headers: { 'content-type': 'text/csv' } })
      return jsonResponse({ id: 'export-1', status: 'QUEUED' }, 202)
    }))
    const client = createBatchClient(scope)

    await client.createExport('batch-1', { format: 'csv', include: ['result'] }, 'etag-r5')
    await client.getExport('export/1')
    const blob = await client.downloadExport('export/1')

    expect(blob).toBeInstanceOf(Blob)
    expect(requests.map(({ url, method }) => [url, method])).toEqual([
      ['/api/v1/batch-predictions/batch-1/exports', 'POST'],
      ['/api/v1/batch-predictions/exports/export%2F1', 'GET'],
      ['/api/v1/batch-predictions/exports/export%2F1/download', 'GET'],
    ])
    expect(requests[0]!.headers.get('If-Match')).toBe('etag-r5')
    expect(requests.filter((request) => request.method === 'POST')).toHaveLength(1)
  })
})
