import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useJobStore } from '@/stores/jobs'
import { createEventStream, EventStreamError } from '@/core/api/sse'
import type { EventStreamOptions } from '@/core/api/sse'

vi.mock('@/core/api/sse', async () => {
  const actual = await vi.importActual<typeof import('@/core/api/sse')>('@/core/api/sse')
  return { ...actual, createEventStream: vi.fn() }
})

describe('Job stream lifecycle', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })
  afterEach(() => { vi.useRealTimers() })

  it('deduplicates by seq and closes after a terminal status', async () => {
    const close = vi.fn(); let options: EventStreamOptions | undefined
    vi.mocked(createEventStream).mockImplementation((_path, nextOptions) => { options = nextOptions; return { close } })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ data: { id: 'job-1', kind: 'prediction', status: 'SUCCEEDED', attempt: 1, cancelable: false, retryable: false, resume_mode: 'none', result_ref: null, error: null, revision: 1, etag: '"v1"', created_at: '', updated_at: '' }, warnings: [], request_id: 'req-1' }), { status: 200, headers: { 'content-type': 'application/json' } }))
    const store = useJobStore(); store.subscribe('job-1')
    options?.onEvent({ seq: '1', type: 'job.progress', schema_version: '1.0', timestamp: '', job_id: 'job-1', data: { completed: 1 } })
    options?.onEvent({ seq: '1', type: 'job.progress', schema_version: '1.0', timestamp: '', job_id: 'job-1', data: { completed: 1 } })
    expect(store.events['job-1']).toHaveLength(1)
    options?.onEvent({ seq: '2', type: 'job.status_changed', schema_version: '1.0', timestamp: '', job_id: 'job-1', data: { status: 'SUCCEEDED' } })
    await Promise.resolve()
    expect(close).toHaveBeenCalled()
  })

  it('keeps polling after 410, discards the expired cursor, and stops at a terminal detail', async () => {
    vi.useFakeTimers()
    let options: EventStreamOptions | undefined
    vi.mocked(createEventStream).mockImplementation((_path, nextOptions) => { options = nextOptions; return { close: vi.fn() } })
    const job = (status: string) => ({ data: { id: 'job-2', kind: 'prediction', status, attempt: 1, cancelable: status === 'RUNNING', retryable: false, resume_mode: 'none', result_ref: null, error: null, revision: 1, etag: '"v1"', created_at: '', updated_at: '' }, warnings: [], request_id: 'req-2' })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(job('RUNNING')), { status: 200, headers: { 'content-type': 'application/json' } })).mockResolvedValueOnce(new Response(JSON.stringify(job('SUCCEEDED')), { status: 200, headers: { 'content-type': 'application/json' } }))
    const store = useJobStore(); store.events['job-2'] = [{ seq: 'expired-seq', type: 'job.progress', schema_version: '1.0', timestamp: '', job_id: 'job-2', data: {} }]
    store.subscribe('job-2'); options?.onError?.(new EventStreamError(410, 'expired'))
    await Promise.resolve(); expect(store.events['job-2']).toEqual([]); expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2000); expect(fetchMock).toHaveBeenCalledTimes(2); expect(store.jobs['job-2']?.status).toBe('SUCCEEDED')
    await vi.advanceTimersByTimeAsync(4000); expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})
