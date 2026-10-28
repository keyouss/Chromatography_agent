import { afterEach, describe, expect, it, vi } from 'vitest'
import { createEventStream, EventStreamError, normalizeBusinessPath, parseSseRecord } from '@/core/api/sse'

describe('SSE transport contract', () => {
  afterEach(() => vi.restoreAllMocks())

  it('accepts MessageAccepted API-prefixed routes and rejects external or non-business routes', () => {
    expect(normalizeBusinessPath('/api/v1/agent-runs/run-1/events/stream')).toBe('/agent-runs/run-1/events/stream')
    expect(normalizeBusinessPath('/jobs/job-1/events/stream?after_seq=4')).toBe('/jobs/job-1/events/stream?after_seq=4')
    expect(() => normalizeBusinessPath('https://evil.invalid/api/v1/jobs/job-1/events/stream')).toThrow()
    expect(() => normalizeBusinessPath('//evil.invalid/api/v1/jobs/job-1/events/stream')).toThrow()
    expect(() => normalizeBusinessPath('/api/v1/health/ready')).toThrow()
    expect(() => normalizeBusinessPath('/api/v1/jobs/job-1/../../settings')).toThrow()
    expect(() => normalizeBusinessPath('/api/v1/jobs/job-1/events/stream/extra')).toThrow()
  })

  it('joins SSE data fields with newlines without trimming payload content', () => {
    expect(parseSseRecord('id: 7\nevent: warning\ndata: first\ndata: second').data).toBe('first\nsecond')
  })

  it('sends Last-Event-ID and reports 410 replay expiry', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('', { status: 410, headers: { 'X-Request-Id': 'req-sse' } }))
    const onError = vi.fn()
    createEventStream('/api/v1/jobs/job-1/events/stream', { afterSeq: '4', lastEventId: '4', onEvent: vi.fn(), onError })
    await new Promise((resolve) => window.setTimeout(resolve, 0))
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get('Last-Event-ID')).toBe('4')
    expect(onError.mock.calls[0]?.[0]).toBeInstanceOf(EventStreamError)
    expect((onError.mock.calls[0]?.[0] as EventStreamError).status).toBe(410)
  })
})
