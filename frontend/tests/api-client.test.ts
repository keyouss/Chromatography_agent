import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { ApiClient } from '@/core/api/client'
import { apiClient } from '@/core/api/client'
import type { ContractCreateConfirmationBody } from '@/core/contract-types'
import type { ArtifactAccepted, Confirmation } from '@/core/types'
import { useRecordsStore } from '@/modules/records/store'

describe('ApiClient stable request headers', () => {
  afterEach(() => vi.restoreAllMocks())
  it('uses /api/v1 and adds request, idempotency and etag headers', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ data: { ok: true }, warnings: [], request_id: 'server-request' }), { status: 200, headers: { 'content-type': 'application/json', 'X-Request-Id': 'server-request' } }))
    const result = await new ApiClient('/api/v1').post<{ ok: boolean }>('/structures/normalize', { value: 'C' }, { requestId: 'req-test', idempotencyKey: 'idem-test', ifMatch: '"etag-test"' })
    expect(result).toEqual({ ok: true }); expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, init] = fetchMock.mock.calls[0]; const headers = new Headers(init?.headers)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/structures/normalize'); expect(headers.get('X-Request-Id')).toBe('req-test'); expect(headers.get('Idempotency-Key')).toBe('idem-test'); expect(headers.get('If-Match')).toBe('"etag-test"')
    expect(init).not.toHaveProperty('idempotencyKey'); expect(init).not.toHaveProperty('ifMatch'); expect(init).not.toHaveProperty('requestId'); expect(init).not.toHaveProperty('contentType')
  })
  it('returns an explicitly raw JSON body while retaining normal envelope unwrapping', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ tools: [], request_id: 'raw-request' }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { ok: true }, warnings: [], request_id: 'envelope-request' }), { status: 200, headers: { 'content-type': 'application/json' } }))
    const client = new ApiClient()

    const raw = await client.getRaw<{ tools: unknown[]; request_id: string }>('/assistant/priority/tools', { requestId: 'req-raw' })
    const enveloped = await client.get<{ ok: boolean }>('/system/version', { requestId: 'req-envelope' })

    expect(raw).toEqual({ tools: [], request_id: 'raw-request' })
    expect(enveloped).toEqual({ ok: true })
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual(['/api/v1/assistant/priority/tools', '/api/v1/system/version'])
    expect(new Headers(fetchMock.mock.calls[0][1]?.headers).get('X-Request-Id')).toBe('req-raw')
    expect(new Headers(fetchMock.mock.calls[1][1]?.headers).get('X-Request-Id')).toBe('req-envelope')
  })
  it('normalizes the API error envelope', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: { code: 'MODEL_UNAVAILABLE', message: '模型不可用' }, request_id: 'req-fail' }), { status: 503, headers: { 'content-type': 'application/json', 'X-Request-Id': 'req-fail' } }))
    await expect(new ApiClient().get('/system/capabilities')).rejects.toMatchObject({ status: 503, code: 'MODEL_UNAVAILABLE', requestId: 'req-fail' })
  })
  it('marks network failures as ambiguous and retains the request id', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(new ApiClient().post('/predictions', { input: {} }, { requestId: 'req-ambiguous' }))
      .rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR', requestId: 'req-ambiguous' })
  })
  it('uses a friendly message for an empty 5xx proxy response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 502 }))
    await expect(new ApiClient().get('/system/capabilities')).rejects.toMatchObject({
      status: 502, code: 'HTTP_502', message: '服务端暂时不可用（HTTP 502）',
    })
  })
  it('retains warnings, page metadata and response headers when requested', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ data: [{ id: 'job-1' }], page: { next_cursor: 'cursor-2', limit: 1, has_more: true }, warnings: [{ code: 'NOTICE', message: 'partial' }], request_id: 'req-page' }), { status: 200, headers: { 'content-type': 'application/json', 'X-Request-Id': 'req-page', ETag: '"jobs-v1"' } }))
    const result = await new ApiClient().requestWithMeta<Array<{ id: string }>>('/jobs')
    expect(result.data).toEqual([{ id: 'job-1' }]); expect(result.page?.next_cursor).toBe('cursor-2'); expect(result.warnings[0]?.code).toBe('NOTICE'); expect(result.response.headers.get('ETag')).toBe('"jobs-v1"')
  })
  it('sends octet-stream upload bodies and returns Blob downloads', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { upload_id: 'u-1', artifact_id: 'a-1', received_at: '', received_bytes: 3, sha256: 'sha' }, warnings: [], request_id: 'req-upload' }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(new Blob(['abc'], { type: 'application/octet-stream' }), { status: 200, headers: { 'content-type': 'application/octet-stream' } }))
    const client = new ApiClient()
    const receipt = await client.requestWithMeta('/artifacts/uploads/u-1/content', { method: 'PUT', body: new Blob(['abc']), contentType: 'application/octet-stream' })
    const download = await client.requestWithMeta<Blob>('/artifacts/a-1/download')
    const [, uploadInit] = fetchMock.mock.calls[0]; const uploadHeaders = new Headers(uploadInit?.headers)
    expect(uploadHeaders.get('Content-Type')).toBe('application/octet-stream')
    expect(uploadInit?.body).toBeInstanceOf(Blob)
    expect(receipt.data).toMatchObject({ upload_id: 'u-1' })
    expect(download.data).toBeInstanceOf(Blob)
    expect(download.data.type).toBe('application/octet-stream')
  })
  it('retains cursor metadata for M2 prediction-record lists', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ data: [], page: { next_cursor: 'record-cursor-2', limit: 20, has_more: true }, warnings: [], request_id: 'req-records' }), { status: 200, headers: { 'content-type': 'application/json' } }))
    const result = await new ApiClient().requestWithMeta<unknown[]>('/prediction-records?cursor=record-cursor-1')
    expect(result.data).toEqual([]); expect(result.page).toMatchObject({ next_cursor: 'record-cursor-2', has_more: true })
  })
  it('requires a project for record listing and recovers after context is resolved', async () => {
    setActivePinia(createPinia())
    const request = vi.spyOn(apiClient, 'requestWithMeta').mockResolvedValue({
      data: [], warnings: [], requestId: 'req-records', response: new Response(null, { status: 200 }),
    })
    const store = useRecordsStore()
    await expect(store.list(null)).resolves.toBeNull()
    expect(request).not.toHaveBeenCalled()
    await store.list('priority-local', 'cursor-1')
    expect(request).toHaveBeenCalledWith('/prediction-records?project_id=priority-local&cursor=cursor-1')
    expect(store.state).toBe('ready')
  })
  it('preserves 202 Location and ETag while serializing complete as an empty object', async () => {
    const accepted: ArtifactAccepted = { id: 'a-1', artifact_id: 'a-1', job_id: 'j-1', resource_ref: { id: 'a-1', type: 'artifact' }, status: 'VALIDATING' }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ data: accepted, warnings: [], request_id: 'req-complete' }), { status: 202, headers: { 'content-type': 'application/json', Location: '/api/v1/artifacts/a-1', ETag: '"artifact-v1"' } }))
    const result = await new ApiClient().requestWithMeta<ArtifactAccepted>('/artifacts/uploads/u-1/complete', { method: 'POST', body: {}, idempotencyKey: 'idem-complete' })
    const [, init] = fetchMock.mock.calls[0]
    expect(init?.body).toBe('{}')
    expect(result.response.status).toBe(202)
    expect(result.location).toBe('/api/v1/artifacts/a-1')
    expect(result.etag).toBe('"artifact-v1"')
  })
  it('sends generated confirmation request headers and preserves response ETag', async () => {
    const body: ContractCreateConfirmationBody = { resource_ref: { id: 'job-1', type: 'job' }, action: 'publish', revision: 3, config_hash: 'a'.repeat(64), policy_version: 'v1' }
    const confirmation: Confirmation = { id: 'confirmation-1', project_id: 'project-1', actor_id: 'actor-1', resource_ref: body.resource_ref, action: body.action, revision: body.revision, config_hash: body.config_hash, policy_version: body.policy_version, status: 'CONFIRMED', created_at: '2026-01-01T00:00:00Z', expires_at: '2026-01-02T00:00:00Z', consumed_at: null, consumed_result_ref: null, summary_snapshot: {} }
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ data: confirmation, warnings: [], request_id: 'req-confirm' }), { status: 201, headers: { 'content-type': 'application/json', ETag: '"confirmation-v2"' } }))
    const result = await new ApiClient().requestWithMeta<Confirmation>('/confirmations', { method: 'POST', body, idempotencyKey: 'idem-confirm', ifMatch: '"preview-v1"' })
    const [, init] = fetchMock.mock.calls[0]; const headers = new Headers(init?.headers)
    expect(headers.get('Idempotency-Key')).toBe('idem-confirm'); expect(headers.get('If-Match')).toBe('"preview-v1"'); expect(headers.get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(init?.body))).toMatchObject({ action: 'publish', revision: 3 }); expect(result.data.id).toBe('confirmation-1'); expect(result.etag).toBe('"confirmation-v2"')
  })
  it('sends data-import validate and commit with frozen headers and accepted locations', async () => {
    const accepted = { data: { id: 'import-1', job_id: 'job-1', resource_ref: { id: 'import-1', type: 'data_import' }, status: 'VALIDATING' }, warnings: [], request_id: 'req-accepted' }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify(accepted), { status: 202, headers: { 'content-type': 'application/json', Location: '/api/v1/jobs/job-1' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify(accepted), { status: 202, headers: { 'content-type': 'application/json', Location: '/api/v1/jobs/job-2' } }))
    const client = new ApiClient()
    const validate = await client.requestWithMeta('/data-imports/import-1/validate?project_id=project-1', { method: 'POST', body: {}, idempotencyKey: 'idem-validate', ifMatch: '"import-v1"' })
    const commit = await client.requestWithMeta('/data-imports/import-1/commit?project_id=project-1', { method: 'POST', body: { confirmation_id: 'confirmation-1' }, idempotencyKey: 'idem-commit' })
    const [, validateInit] = fetchMock.mock.calls[0]; const validateHeaders = new Headers(validateInit?.headers)
    const [, commitInit] = fetchMock.mock.calls[1]; const commitHeaders = new Headers(commitInit?.headers)
    expect(validateHeaders.get('Idempotency-Key')).toBe('idem-validate'); expect(validateHeaders.get('If-Match')).toBe('"import-v1"'); expect(validateInit?.body).toBe('{}'); expect(validate.location).toBe('/api/v1/jobs/job-1')
    expect(commitHeaders.get('Idempotency-Key')).toBe('idem-commit'); expect(JSON.parse(String(commitInit?.body))).toEqual({ confirmation_id: 'confirmation-1' }); expect(commit.location).toBe('/api/v1/jobs/job-2')
  })
})
