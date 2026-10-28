import { beforeEach, describe, expect, it, vi } from 'vitest'
import { clearTlcBatchPointer, createTlcBatchClient, readTlcBatchPointer, saveTlcBatchPointer, type TlcBatchCreate } from '../candidates/tlc-batch-revision20261008-01/src/modules/prediction/forms/tlc/tlc-batch-client'

const draftBody: TlcBatchCreate = {
  project_id: 'project-test', name: 'TLC candidate', schema_version: '1.0',
  model_selection: { mode: 'exact', model_id: 'model-author', model_version: 'v1.1' },
  input_artifact_id: 'artifact-server-issued', template_version: 'tlc-batch-1',
  field_mapping: { structure_ref: 'structure_ref', solvent_components: 'solvent_components' },
  common_input: { solvent_components: null }, invalid_row_policy: 'block',
  resource_budget: { max_evaluations: 10, max_runtime_seconds: 120 },
  chromatography_type: 'tlc', tool: 'batch',
}

function jsonResponse(data: unknown, status = 200, etag?: string) {
  return new Response(JSON.stringify({ data, request_id: 'request-test', warnings: [] }), {
    status,
    headers: { 'content-type': 'application/json', ...(etag ? { ETag: etag } : {}) },
  })
}

function captureRequest(input: RequestInfo | URL, init?: RequestInit) {
  return { url: String(input), method: init?.method ?? 'GET', headers: new Headers(init?.headers), body: init?.body }
}

beforeEach(() => { vi.restoreAllMocks(); sessionStorage.clear() })

describe('TLC batch candidate client', () => {
  it('reuses an idempotency key for the same draft payload and rotates it when the template version changes', async () => {
    const requests: ReturnType<typeof captureRequest>[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(captureRequest(input, init))
      return jsonResponse({ id: 'batch-1' }, 201, 'draft-etag-1')
    }))
    const client = createTlcBatchClient()

    await client.createDraft(draftBody)
    await client.createDraft(draftBody)
    await client.createDraft({ ...draftBody, template_version: 'tlc-batch-next' })
    const omittedComponent = { ...draftBody, common_input: {} }
    const undefinedComponent = { ...draftBody, common_input: { solvent_components: undefined } }
    const reorderedOmitted = Object.fromEntries(Object.entries(omittedComponent).reverse()) as TlcBatchCreate
    await client.createDraft(omittedComponent)
    await client.createDraft(undefinedComponent)
    await client.createDraft(reorderedOmitted)

    const keys = requests.map((request) => request.headers.get('Idempotency-Key'))
    expect(requests.map((request) => request.url)).toEqual(Array(6).fill('/api/v1/batch-predictions/drafts'))
    expect(keys[0]).toBeTruthy()
    expect(keys[1]).toBe(keys[0])
    expect(keys[2]).not.toBe(keys[0])
    expect(keys[3]).not.toBe(keys[0])
    expect(keys[4]).toBe(keys[3])
    expect(keys[5]).toBe(keys[3])
    expect(JSON.parse(requests[3]!.body as string).common_input).toEqual({})
    expect(JSON.parse(requests[4]!.body as string).common_input).toEqual({})
    expect(readTlcBatchPointer('project-test')).toBeNull()
  })

  it('keeps only a caller-validated server pointer and tolerates unavailable session storage', () => {
    saveTlcBatchPointer('project-test', 'batch-server-id')
    expect(readTlcBatchPointer('project-test')).toBe('batch-server-id')
    expect(readTlcBatchPointer('another-project')).toBeNull()
    expect(JSON.parse(sessionStorage.getItem('tlc-batch-candidate.active.v1')!)).toEqual({ project_id: 'project-test', batch_id: 'batch-server-id' })
    clearTlcBatchPointer()
    expect(readTlcBatchPointer('project-test')).toBeNull()

    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage disabled') })
    expect(() => saveTlcBatchPointer('project-test', 'batch-server-id')).not.toThrow()
    setItem.mockRestore()
  })

  it('requests the explicit template version and rejects a mismatched response version', async () => {
    const requests: ReturnType<typeof captureRequest>[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(captureRequest(input, init))
      const url = String(input)
      const version = url.includes('model_id=model-b') ? 'tlc-batch-1' : 'tlc-batch-2'
      return new Response('structure_ref,solvent_components\n', {
        status: 200,
        headers: { 'content-type': 'text/csv', 'X-Template-Version': version, 'X-Template-SHA256': 'a'.repeat(64) },
      })
    }))
    const client = createTlcBatchClient()

    const template = await client.downloadTemplate('model-a', 'tlc-batch-2', 'csv')
    expect(template.version).toBe('tlc-batch-2')
    expect(requests[0]!.url).toBe('/api/v1/prediction/types/tlc/batch-template?tool=batch&model_id=model-a&template_version=tlc-batch-2&format=csv')
    await expect(client.downloadTemplate('model-b', 'tlc-batch-2', 'csv')).rejects.toThrow('版本与所选版本不一致')
  })

  it('fails closed when type or batch-tool capability literals do not match TLC', async () => {
    const responses = [
      { id: 'cc' },
      { id: 'batch', chromatography_type: 'cc', templates: [] },
      { id: 'single', schema_version: '1.1' },
    ]
    const urls: string[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      urls.push(String(input))
      return jsonResponse(responses.shift())
    }))
    const client = createTlcBatchClient()

    await expect(client.getTypeCapability()).rejects.toThrow('与 TLC 不一致')
    await expect(client.getToolCapability('1.1')).rejects.toThrow('与 TLC 批量工具不一致')
    await expect(client.getToolCapability('1.1')).rejects.toThrow('与 TLC 批量工具不一致')
    expect(urls).toEqual([
      '/api/v1/prediction/types/tlc',
      '/api/v1/prediction/types/tlc/tools/batch/schema?schema_version=1.1',
      '/api/v1/prediction/types/tlc/tools/batch/schema?schema_version=1.1',
    ])
  })

  it('binds validation and patch keys to the server ETag and deduplicates concurrent validation', async () => {
    const requests: ReturnType<typeof captureRequest>[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(captureRequest(input, init))
      await Promise.resolve()
      return jsonResponse({ id: 'validation-1', job_id: 'job-1', resource_ref: { type: 'batch_validation', id: 'validation-1' }, status: 'QUEUED' }, 202)
    }))
    const client = createTlcBatchClient()
    const [first, concurrent] = await Promise.all([client.validate('batch-1', 'etag-r1'), client.validate('batch-1', 'etag-r1')])
    await client.validate('batch-1', 'etag-r1')
    await client.validate('batch-1', 'etag-r2')
    await client.patchDraft('batch-1', { name: 'renamed' }, 'etag-r1')

    expect(first.data).toEqual(concurrent.data)
    expect(requests.filter((request) => request.url.endsWith('/validate'))).toHaveLength(3)
    const validations = requests.filter((request) => request.url.endsWith('/validate'))
    expect(validations.map((request) => request.headers.get('If-Match'))).toEqual(['etag-r1', 'etag-r1', 'etag-r2'])
    expect(validations[0]!.headers.get('Idempotency-Key')).toBe(validations[1]!.headers.get('Idempotency-Key'))
    expect(validations[2]!.headers.get('Idempotency-Key')).not.toBe(validations[0]!.headers.get('Idempotency-Key'))
    const patch = requests.at(-1)!
    expect(patch.method).toBe('PATCH')
    expect(patch.headers.get('If-Match')).toBe('etag-r1')
    expect(patch.headers.get('Idempotency-Key')).toBeTruthy()
  })

  it('requires a server confirmation and sends it once for a concurrent submit', async () => {
    const requests: ReturnType<typeof captureRequest>[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(captureRequest(input, init))
      await Promise.resolve()
      return jsonResponse({ id: 'batch-1', job_id: 'job-1', attempt: 1, execution_status: 'QUEUED', resource_ref: { type: 'batch_prediction', id: 'batch-1' } }, 202)
    }))
    const client = createTlcBatchClient()

    await expect(client.submit('batch-1', '')).rejects.toThrow('服务端确认记录')
    expect(requests).toHaveLength(0)
    await Promise.all([client.submit('batch-1', 'confirmation-1'), client.submit('batch-1', 'confirmation-1')])

    expect(requests).toHaveLength(1)
    expect(requests[0]!.url).toBe('/api/v1/batch-predictions/batch-1/submit')
    expect(requests[0]!.headers.get('Idempotency-Key')).toBeTruthy()
    expect(JSON.parse(requests[0]!.body as string)).toEqual({ confirmation_id: 'confirmation-1' })
  })

  it('reads lifecycle resources and cancels with the current ETag', async () => {
    const requests: ReturnType<typeof captureRequest>[] = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      requests.push(captureRequest(input, init))
      const url = String(input)
      if (url.endsWith('/validation')) return jsonResponse({ id: 'validation-1', status: 'READY' })
      if (url.includes('/validation/rows')) return jsonResponse({ rows: [], has_more: false })
      if (url.includes('/rows')) return jsonResponse({ rows: [], has_more: false })
      return jsonResponse({ id: 'batch-1', draft_status: 'DRAFT' }, 200, 'etag-canceled')
    }))
    const client = createTlcBatchClient()

    await client.getDraft('batch-1')
    await client.getValidation('batch-1')
    await client.listValidationRows('batch-1', { status: 'INVALID', limit: 20 })
    await client.listRows('batch-1', { status: 'SKIPPED', cursor: 'next', limit: 5 })
    await client.cancel('batch-1', 'etag-r3', 'operator request')

    expect(requests.map((request) => request.url)).toEqual([
      '/api/v1/batch-predictions/batch-1',
      '/api/v1/batch-predictions/batch-1/validation',
      '/api/v1/batch-predictions/batch-1/validation/rows?status=INVALID&limit=20',
      '/api/v1/batch-predictions/batch-1/rows?status=SKIPPED&cursor=next&limit=5',
      '/api/v1/batch-predictions/batch-1/cancel',
    ])
    expect(requests.at(-1)!.method).toBe('POST')
    expect(requests.at(-1)!.headers.get('If-Match')).toBe('etag-r3')
    expect(JSON.parse(requests.at(-1)!.body as string)).toEqual({ reason: 'operator request' })
    expect(requests.at(-1)!.headers.get('Idempotency-Key')).toBeTruthy()
  })
})
