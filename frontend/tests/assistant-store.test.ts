import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ContractAgentRun, ContractConversation } from '@/core/contract-types'
import { useAssistantStore } from '@/modules/assistant/store'
import { createEventStream } from '@/core/api/sse'

vi.mock('@/core/api/sse', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/core/api/sse')>(),
  createEventStream: vi.fn(() => ({ close: vi.fn() })),
}))

function jsonResponse(data: unknown, page: unknown = { has_more: false, next_cursor: null }) {
  return new Response(JSON.stringify({ data, page, request_id: 'assistant-store-test', warnings: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
}

function errorResponse(status: number, code: string) {
  return new Response(JSON.stringify({ error: { code, message: `synthetic ${code}` }, request_id: 'assistant-store-test' }), { status, headers: { 'content-type': 'application/json' } })
}

function conversation(id: string, title = id, status: 'ACTIVE' | 'ARCHIVED' = 'ACTIVE', etag = `${id}-etag`, projectId = 'project-1'): ContractConversation {
  return {
    id, project_id: projectId, owner_id: 'actor-1', mode: 'business', status, title, etag, revision: 1,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  }
}

function freshStore() {
  setActivePinia(createPinia())
  const store = useAssistantStore()
  store.projectId = 'project-1'
  return store
}

function stubLocalStorage() {
  const entries = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => entries.get(key) ?? null,
    setItem: (key: string, value: string) => { entries.set(key, value) },
    removeItem: (key: string) => { entries.delete(key) },
    clear: () => { entries.clear() },
  } satisfies Partial<Storage>)
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('assistant conversation list state', () => {
  it.each([
    ['QUEUED', 'RUNNING'], ['CANCEL_REQUESTED', 'CANCEL_REQUESTED'], ['CANCELED', 'CANCELED'],
  ])('refreshes authoritative status on llm.started from %s without inferring RUNNING', async (initial, current) => {
    vi.useFakeTimers()
    const store = freshStore()
    store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-status', conversation_id: 'conversation-1', status: initial } as ContractAgentRun
    vi.mocked(createEventStream).mockClear()
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => String(input).endsWith('/agent-runs/run-status')
      ? jsonResponse({ id: 'run-status', conversation_id: 'conversation-1', status: current })
      : jsonResponse([]))
    store.startEventStream('run-status')
    vi.mocked(createEventStream).mock.calls[0]![1].onEvent({ type: 'llm.started', seq: '2', data: {} } as never)
    await vi.advanceTimersByTimeAsync(100)
    await flushPromises()
    expect(fetch.mock.calls.some(([input]) => String(input).endsWith('/agent-runs/run-status'))).toBe(true)
    expect(store.run?.status).toBe(current)
    store.stopEventStream()
  })

  it('keeps cancellation subscribed until the authoritative canceled event and snapshot arrive', async () => {
    const store = freshStore()
    store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'running-etag' } as ContractAgentRun
    vi.mocked(createEventStream).mockClear()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/cancel')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'CANCEL_REQUESTED', etag: 'cancel-etag' })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'CANCELED', etag: 'canceled-etag' })
      return jsonResponse([])
    })
    await store.cancelRun('run-1', 'running-etag')
    expect(store.run?.status).toBe('CANCEL_REQUESTED')
    expect(createEventStream).toHaveBeenCalledOnce()
    vi.mocked(createEventStream).mock.calls[0]![1].onEvent({ type: 'run.canceled', seq: '20', data: {} } as never)
    await flushPromises()
    expect(store.run?.status).toBe('CANCELED')
    expect(vi.mocked(createEventStream).mock.results[0]!.value.close).toHaveBeenCalled()
    store.stopEventStream()
  })

  it('ignores an older search response after a newer query has completed', async () => {
    const store = freshStore()
    let resolveFirst!: (response: Response) => void
    const firstResponse = new Promise<Response>((resolve) => { resolveFirst = resolve })
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => String(input).includes('q=first')
      ? firstResponse
      : Promise.resolve(jsonResponse([conversation('second', 'second query')])) )

    const firstRequest = store.listConversations({ q: 'first' })
    await store.listConversations({ q: 'second' })
    resolveFirst(jsonResponse([conversation('first', 'first query')]))

    expect(await firstRequest).toBeNull()
    expect(store.searchResults.map((item) => item.id)).toEqual(['second'])
  })

  it('keeps archived and active-search responses in separate request buckets', async () => {
    const store = freshStore()
    let resolveSearch!: (response: Response) => void
    const searchResponse = new Promise<Response>((resolve) => { resolveSearch = resolve })
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => String(input).includes('archived=true')
      ? Promise.resolve(jsonResponse([conversation('archived', 'archived', 'ARCHIVED')]))
      : searchResponse)

    const searchRequest = store.listConversations({ q: 'active' })
    await store.listConversations({ archived: true })
    resolveSearch(jsonResponse([conversation('active-search')]))
    await searchRequest

    expect(store.archivedConversations.map((item) => item.id)).toEqual(['archived'])
    expect(store.searchResults.map((item) => item.id)).toEqual(['active-search'])
  })

  it('does not clear the current selection while appending a normal-list page', async () => {
    const store = freshStore()
    store.selectedConversationId = 'selected'
    store.conversations = [conversation('selected')]
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([conversation('next-page')], { has_more: false, next_cursor: null }))

    await store.listConversations({ cursor: 'cursor-2', append: true })

    expect(store.selectedConversationId).toBe('selected')
    expect(store.conversations.map((item) => item.id)).toEqual(['selected', 'next-page'])
  })

  it('renames and archives an active search result with its current project and ETag', async () => {
    const store = freshStore()
    store.searchResults = [conversation('searched', 'before', 'ACTIVE', 'search-etag')]
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse(conversation('searched', 'after', 'ACTIVE', 'renamed-etag')))
      .mockResolvedValueOnce(jsonResponse(conversation('searched', 'after', 'ARCHIVED', 'archived-etag')))

    await store.renameConversation('searched', 'after')
    await store.archiveConversation('searched', 'confirmed-1')

    expect(fetch.mock.calls[0]?.[0]).toBe('/api/v1/conversations/searched')
    expect((fetch.mock.calls[0]?.[1]?.headers as Headers).get('If-Match')).toBe('search-etag')
    expect(fetch.mock.calls[1]?.[0]).toBe('/api/v1/conversations/searched/archive')
    expect(store.searchResults).toEqual([])
    expect(store.archivedConversations.map((item) => item.id)).toEqual(['searched'])
  })

  it('rejects a mutation candidate from another project', async () => {
    const store = freshStore()
    store.searchResults = [conversation('foreign', 'foreign', 'ACTIVE', 'foreign-etag', 'project-2')]
    const fetch = vi.spyOn(globalThis, 'fetch')

    await expect(store.renameConversation('foreign', 'renamed')).rejects.toThrow('当前会话不可重命名')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('replays a frozen delete attempt even after refresh removed the conversation from all buckets', async () => {
    const store = freshStore()
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockRejectedValueOnce(new TypeError('connection reset after server commit'))
      .mockResolvedValueOnce(jsonResponse({ id: 'deleted', deleted: true }))
    const frozenBody = { confirmation_id: 'confirmation-1' }

    await expect(store.deleteConversation('deleted', frozenBody, 'key-1', 'preview-etag', 'project-1')).rejects.toThrow('connection reset')
    store.conversations = []
    store.searchResults = []
    store.archivedConversations = []
    await store.deleteConversation('deleted', frozenBody, 'key-1', 'preview-etag', 'project-1')

    expect(fetch).toHaveBeenCalledTimes(2)
    for (const [, init] of fetch.mock.calls) {
      const headers = init?.headers as Headers
      expect(headers.get('If-Match')).toBe('preview-etag')
      expect(headers.get('Idempotency-Key')).toBe('key-1')
      expect(JSON.parse(String(init?.body))).toEqual(frozenBody)
    }
  })

  it('uses the delete preview ETag and clears current assistant state only after success', async () => {
    const store = freshStore()
    store.conversations = [conversation('to-delete', 'active', 'ACTIVE', 'stale-active-etag')]
    store.searchResults = [conversation('to-delete', 'search', 'ACTIVE', 'stale-search-etag')]
    store.archivedConversations = [conversation('to-delete', 'archived', 'ARCHIVED', 'stale-archived-etag')]
    store.selectedConversationId = 'to-delete'
    store.messages = [{ id: 'message', conversation_id: 'to-delete', role: 'user', blocks: [{ type: 'text', text: 'kept until success' }] } as never]
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ id: 'to-delete', deleted: true }))

    await store.deleteConversation('to-delete', { confirmation_id: 'confirmation-2' }, 'key-2', 'preview-etag', 'project-1')

    expect((fetch.mock.calls[0]?.[1]?.headers as Headers).get('If-Match')).toBe('preview-etag')
    expect(store.conversations).toEqual([])
    expect(store.searchResults).toEqual([])
    expect(store.archivedConversations).toEqual([])
    expect(store.selectedConversationId).toBeNull()
    expect(store.messages).toEqual([])
  })

  it('does not revive deleted state from delayed list, message, or SSE responses', async () => {
    stubLocalStorage()
    const store = freshStore()
    localStorage.clear()
    store.conversations = [conversation('late-delete')]
    store.selectedConversationId = 'late-delete'
    let resolveList!: (response: Response) => void
    let resolveMessages!: (response: Response) => void
    const pendingList = new Promise<Response>((resolve) => { resolveList = resolve })
    const pendingMessages = new Promise<Response>((resolve) => { resolveMessages = resolve })
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (init?.method === 'DELETE') return Promise.resolve(jsonResponse({ id: 'late-delete', deleted: true }))
      if (url.includes('/messages')) return pendingMessages
      if (url.includes('/conversations?')) return pendingList
      throw new Error(`Unexpected GET ${url}`)
    })
    const listRequest = store.listConversations()
    const currentRequest = store.loadConversation('late-delete')
    vi.mocked(createEventStream).mockClear()
    store.startEventStream('late-run')
    const lateEvent = vi.mocked(createEventStream).mock.calls[0]![1].onEvent
    store.streamTextProjections = [{ conversation_id: 'late-delete', run_id: 'late-run', message_id: 'message', block_index: 0, text: 'partial', codePointLength: 7, revision: 1, incomplete: false }] as never
    store.events = { events: [{ seq: '1' }] } as never
    store.toolCalls = [{ id: 'tool' }] as never
    store.predictionResults = { prediction: {} } as never
    store.registrationDrafts = { registration: {} } as never
    expect(JSON.parse(localStorage.getItem('chrom-platform.assistant') ?? '{}')).toMatchObject({ projectId: 'project-1', conversationId: 'late-delete' })

    await store.deleteConversation('late-delete', { confirmation_id: 'delete-confirmation' }, 'delete-key', 'preview-etag', 'project-1')
    lateEvent({ type: 'warning', seq: '99', data: { message: 'stale' } } as never)
    resolveList(jsonResponse([conversation('late-delete')]))
    resolveMessages(jsonResponse([{ id: 'late-message', conversation_id: 'late-delete', role: 'user', blocks: [] }]))
    await Promise.all([listRequest, currentRequest])

    expect(store.conversations).toEqual([])
    expect(store.selectedConversationId).toBeNull()
    expect(store.messages).toEqual([])
    expect(store.streamTextProjections).toEqual([])
    expect(store.events).toBeNull()
    expect(store.liveEvents).toEqual([])
    expect(store.toolCalls).toEqual([])
    expect(store.predictionResults).toEqual({})
    expect(store.registrationDrafts).toEqual({})
    expect(JSON.parse(localStorage.getItem('chrom-platform.assistant') ?? '{}')).toMatchObject({ projectId: 'project-1', conversationId: null })
    expect(vi.mocked(createEventStream).mock.results[0]!.value.close).toHaveBeenCalledOnce()
  })

  it('deletes a non-current archived conversation without clearing current state or its stream', async () => {
    stubLocalStorage()
    const store = freshStore()
    store.conversations = [conversation('current')]
    store.archivedConversations = [conversation('archived-delete', 'archived', 'ARCHIVED', 'archive-list-etag')]
    store.selectedConversationId = 'current'
    store.run = { id: 'current-run', conversation_id: 'current', status: 'RUNNING' } as ContractAgentRun
    store.messages = [{ id: 'current-message', conversation_id: 'current', role: 'user', blocks: [] }] as never
    localStorage.setItem('chrom-platform.assistant', JSON.stringify({ projectId: 'project-1', conversationId: 'current' }))
    vi.mocked(createEventStream).mockClear()
    store.startEventStream('current-run')
    const stream = vi.mocked(createEventStream).mock.results[0]!.value
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ id: 'archived-delete', deleted: true }))

    await store.deleteConversation('archived-delete', { confirmation_id: 'confirmation-archive-delete' }, 'key-archive-delete', 'preview-etag', 'project-1')

    expect((fetch.mock.calls[0]?.[1]?.headers as Headers).get('If-Match')).toBe('preview-etag')
    expect(store.archivedConversations).toEqual([])
    expect(store.selectedConversationId).toBe('current')
    expect(store.messages.map((item) => item.id)).toEqual(['current-message'])
    expect(store.run?.id).toBe('current-run')
    expect(localStorage.getItem('chrom-platform.assistant')).toContain('current')
    expect(stream.close).not.toHaveBeenCalled()
  })

  it.each([[409, 'CONVERSATION_BUSY'], [412, 'ETAG_MISMATCH'], [404, 'RESOURCE_NOT_FOUND']])('preserves lists and current messages after DELETE %s %s', async (status, code) => {
    const store = freshStore()
    store.conversations = [conversation('failed-delete')]
    store.selectedConversationId = 'failed-delete'
    store.messages = [{ id: 'preserved-message', conversation_id: 'failed-delete', role: 'user', blocks: [] }] as never
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(errorResponse(status, code))

    await expect(store.deleteConversation('failed-delete', { confirmation_id: 'confirmation-failed' }, 'key-failed', 'preview-etag', 'project-1')).rejects.toMatchObject({ status, code })

    expect(store.conversations.map((item) => item.id)).toEqual(['failed-delete'])
    expect(store.selectedConversationId).toBe('failed-delete')
    expect(store.messages.map((item) => item.id)).toEqual(['preserved-message'])
    expect(fetch).toHaveBeenCalledOnce()
  })

  it('blocks a known non-terminal current run without sending DELETE', async () => {
    const store = freshStore()
    store.conversations = [conversation('busy-delete')]
    store.selectedConversationId = 'busy-delete'
    store.run = { id: 'busy-run', conversation_id: 'busy-delete', status: 'RUNNING' } as ContractAgentRun
    const fetch = vi.spyOn(globalThis, 'fetch')

    await expect(store.deleteConversation('busy-delete', { confirmation_id: 'confirmation-busy' }, 'key-busy', 'preview-etag', 'project-1')).rejects.toMatchObject({ code: 'CONVERSATION_BUSY' })

    expect(fetch).not.toHaveBeenCalled()
    expect(store.run?.status).toBe('RUNNING')
  })


})
