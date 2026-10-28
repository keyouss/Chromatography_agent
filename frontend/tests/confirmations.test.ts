import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useConfirmationStore } from '@/stores/confirmations'
import type { Confirmation } from '@/core/types'

function previewResponse(id: string, revision = 1) {
  return new Response(JSON.stringify({ data: { resource_ref: { id, type: 'prediction_record' }, action: 'archive', revision, config_hash: 'a'.repeat(64), etag: `"preview-${id}-${revision}"`, summary: [], dependencies: [], impact: {}, can_confirm: true, blockers: [], policy_version: 'v1' }, warnings: [], request_id: `req-${id}` }), { status: 200, headers: { 'content-type': 'application/json' } })
}

describe('confirmation resource binding', () => {
  beforeEach(() => { setActivePinia(createPinia()) })
  afterEach(() => vi.restoreAllMocks())

  it('ignores late preview responses after switching resources', async () => {
    let resolveOld!: (response: Response) => void
    const oldResponse = new Promise<Response>((resolve) => { resolveOld = resolve })
    vi.spyOn(globalThis, 'fetch').mockReturnValueOnce(oldResponse).mockResolvedValueOnce(previewResponse('record-b'))
    const store = useConfirmationStore()
    const oldRequest = store.loadPreview({ id: 'record-a', type: 'prediction_record' }, 'archive')
    await store.loadPreview({ id: 'record-b', type: 'prediction_record' }, 'archive')
    resolveOld(previewResponse('record-a'))
    await oldRequest
    expect(store.preview?.resource_ref.id).toBe('record-b')
    expect(store.state).toBe('ready')
    expect(store.error).toBeNull()
    expect(store.confirmation).toBeNull()
  })

  it('ignores late confirmation responses after the same resource revision changes', async () => {
    let resolveOld!: (response: Response) => void
    const oldResponse = new Promise<Response>((resolve) => { resolveOld = resolve })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(previewResponse('record-a')).mockReturnValueOnce(oldResponse).mockResolvedValueOnce(previewResponse('record-a', 2))
    const store = useConfirmationStore()
    await store.loadPreview({ id: 'record-a', type: 'prediction_record' }, 'archive')
    const oldRequest = store.create()
    await store.loadPreview({ id: 'record-a', type: 'prediction_record' }, 'archive')
    const oldConfirmation: Confirmation = { id: 'confirmation-old', project_id: 'project-1', actor_id: 'actor-1', resource_ref: { id: 'record-a', type: 'prediction_record' }, action: 'archive', revision: 1, config_hash: 'a'.repeat(64), policy_version: 'v1', status: 'CONFIRMED', created_at: '2026-01-01T00:00:00Z', expires_at: '2026-01-02T00:00:00Z', consumed_at: null, consumed_result_ref: null, summary_snapshot: {} }
    resolveOld(new Response(JSON.stringify({ data: oldConfirmation, warnings: [], request_id: 'req-confirm-old' }), { status: 201, headers: { 'content-type': 'application/json', ETag: '"confirmation-old"' } }))
    await oldRequest
    expect(store.preview?.revision).toBe(2)
    expect(store.confirmation).toBeNull()
    expect(store.etag).toBeNull()
    expect(store.state).toBe('ready')
    expect(new Headers(fetchMock.mock.calls[1]![1]?.headers).get('If-Match')).toBe('"preview-record-a-1"')
  })

  it('creates a confirmation for A and clears it before switching to B', async () => {
    const response = (id: string) => new Response(JSON.stringify({ data: { resource_ref: { id, type: 'prediction_record' }, action: 'archive', revision: 1, config_hash: 'a'.repeat(64), etag: `"preview-${id}"`, summary: [], dependencies: [], impact: {}, can_confirm: true, blockers: [], policy_version: 'v1' }, warnings: [], request_id: `req-${id}` }), { status: 200, headers: { 'content-type': 'application/json' } })
    const confirmation: Confirmation = { id: 'confirmation-a', project_id: 'project-1', actor_id: 'actor-1', resource_ref: { id: 'record-a', type: 'prediction_record' }, action: 'archive', revision: 1, config_hash: 'a'.repeat(64), policy_version: 'v1', status: 'CONFIRMED', created_at: '2026-01-01T00:00:00Z', expires_at: '2026-01-02T00:00:00Z', consumed_at: null, consumed_result_ref: null, summary_snapshot: {} }
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(response('record-a')).mockResolvedValueOnce(new Response(JSON.stringify({ data: confirmation, warnings: [], request_id: 'req-confirm' }), { status: 201, headers: { 'content-type': 'application/json', ETag: '"confirmation-a"' } })).mockResolvedValueOnce(response('record-b'))
    const store = useConfirmationStore()
    await store.loadPreview({ id: 'record-a', type: 'prediction_record' }, 'archive')
    expect(store.previewIsFor({ id: 'record-a', type: 'prediction_record' }, 'archive')).toBe(true)
    expect(store.isFor({ id: 'record-a', type: 'prediction_record' }, 'archive')).toBe(false)
    await store.create()
    expect(store.confirmation?.id).toBe('confirmation-a')
    expect(store.isFor({ id: 'record-a', type: 'prediction_record' }, 'archive')).toBe(true)
    await store.loadPreview({ id: 'record-b', type: 'prediction_record' }, 'archive')
    expect(store.preview?.resource_ref.id).toBe('record-b'); expect(store.confirmation).toBeNull(); expect(store.isFor({ id: 'record-a', type: 'prediction_record' }, 'archive')).toBe(false); expect(store.isFor({ id: 'record-b', type: 'prediction_record' }, 'archive')).toBe(false); expect(store.previewIsFor({ id: 'record-a', type: 'prediction_record' }, 'archive')).toBe(false); expect(store.previewIsFor({ id: 'record-b', type: 'prediction_record' }, 'archive')).toBe(true)
  })
})
