import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { ContractAccessContext, ContractAgentRun, ContractConversation } from '@/core/contract-types'
import { assistantFailureNotice, assistantPredictionResourceId, assistantRegistrationDraftResourceId, assistantRegistrationDraftPath, assistantToolGroup, canSendAssistantMessage, makeResumeBody, structureToolSummary, toolResultSummaryEntries, useAssistantStore } from './store'

type PendingBinding = NonNullable<ContractAgentRun['pending_actions']>[number]

function jsonResponse(data: unknown, status = 200, extraHeaders: Record<string, string> = {}) {
  return new Response(JSON.stringify({ data, request_id: 'assistant-test', warnings: [] }), { status, headers: { 'content-type': 'application/json', ...extraHeaders } })
}

function rawJsonResponse(data: unknown, status = 200, extraHeaders: Record<string, string> = {}) {
  return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json', ...extraHeaders } })
}

function binding(confirmationId?: string): PendingBinding {
  return { action: 'create_registration_draft', config_hash: 'a'.repeat(64), confirmation_id: confirmationId, expires_at: null, normalized_args: {}, resource_ref: { type: 'model_registration', id: 'resource-1' }, revision: 1, schema_version: '1.0', tool_call_id: 'tool-1' }
}

function confirmation(overrides: Record<string, unknown> = {}) {
  return { id: 'confirmation-1', project_id: 'project-1', actor_id: 'actor-1', resource_ref: { type: 'model_registration', id: 'resource-1' }, action: 'create_registration_draft', revision: 1, config_hash: 'a'.repeat(64), policy_version: 'policy-1', summary_snapshot: {}, status: 'CONFIRMED', created_at: '2026-01-01T00:00:00Z', expires_at: new Date(Date.now() + 60_000).toISOString(), consumed_at: null, consumed_result_ref: null, ...overrides }
}

function requestBody(request: { init?: RequestInit }) { return JSON.parse(String(request.init?.body)) as Record<string, unknown> }

function assistantEventStream() {
  let controller: ReadableStreamDefaultController<Uint8Array> | null = null
  const body = new ReadableStream<Uint8Array>({ start(value) { controller = value } })
  return {
    body,
    get ready() { return controller !== null },
    emit(seq: string, type: string, data: Record<string, unknown>) {
      controller?.enqueue(new TextEncoder().encode(`id: ${seq}\ndata: ${JSON.stringify({ seq, type, schema_version: '1.0', timestamp: '2026-10-03T00:00:00Z', agent_run_id: 'run-1', data })}\n\n`))
    },
  }
}

async function flushAssistantMicrotasks() {
  for (let index = 0; index < 8; index += 1) await vi.advanceTimersByTimeAsync(0)
}

function conversation(id: string, projectId: string, currentRunId: string | null = null, mode: ContractConversation['mode'] = 'business'): ContractConversation {
  return { id, project_id: projectId, mode, title: id, owner_id: 'actor-1', status: 'ACTIVE', revision: 1, etag: `${id}-etag`, created_at: '2026-10-03T00:00:00Z', updated_at: '2026-10-03T00:00:00Z', last_message_at: null, current_run_id: currentRunId }
}

function accessContext(projectId: string): ContractAccessContext {
  return { actor: { id: 'actor-1', auth_mode: 'deployment_default', policy_version: 'policy-1' }, actor_id: 'actor-1', auth_mode: 'deployment_default', allowed_actions: ['*'], is_multi_user_authenticated: false, permissions: ['*'], project_selection: 'resolved', accessible_projects: [{ id: projectId, name: projectId }], default_project_id: projectId }
}

function installLocalStorage() {
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
    clear: () => { values.clear() },
  } })
}

describe('business assistant UI boundaries', () => {
  beforeEach(() => { setActivePinia(createPinia()); installLocalStorage() })
  afterEach(() => vi.restoreAllMocks())

  it('keeps the first launch tool scope explicit', () => {
    expect(assistantToolGroup('create_prediction')).toBe('prediction')
    expect(assistantToolGroup('get_data_version_summary')).toBe('data')
    expect(assistantToolGroup('create_registration_draft')).toBe('model')
    expect(assistantToolGroup('normalize_structure')).toBe('structure')
    expect(assistantToolGroup('search_knowledge')).toBeNull()
    expect(assistantToolGroup('publish')).toBeNull()
    expect(assistantToolGroup('submit_training')).toBeNull()
  })

  it('loads first-round capabilities from the service tool catalog', async () => {
    const requests: { url: string; headers: Headers }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      requests.push({ url: String(input), headers: new Headers(init?.headers) })
      return rawJsonResponse({ request_id: 'assistant-test', tools: [{ description: '提交单次预测', name: 'create_prediction', parameters: {}, permission: 'assistant:run', requires_confirmation: false, schema_version: '1.0' }] })
    })
    const store = useAssistantStore()

    const tools = await store.listPriorityTools()

    expect(requests[0]?.url).toBe('/api/v1/assistant/priority/tools')
    expect(requests[0]?.headers.get('Accept')).toBe('application/json')
    expect(requests[0]?.headers.get('X-Request-Id')).toMatch(/^req-/)
    expect(tools.map((tool) => tool.name)).toEqual(['create_prediction'])
    expect(store.priorityToolsState).toBe('ready')
    expect(store.priorityToolsError).toBeNull()
  })

  it('records a real tool-catalog failure without keeping stale capabilities', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: '工具目录暂不可用' } }), { status: 404, headers: { 'content-type': 'application/json' } }))
    const store = useAssistantStore()
    store.priorityTools = [{ description: 'old', name: 'create_prediction', parameters: {}, permission: 'assistant:run', requires_confirmation: false, schema_version: '1.0' }]

    await expect(store.listPriorityTools()).resolves.toEqual([])

    expect(store.priorityToolsState).toBe('error')
    expect(store.priorityTools).toEqual([])
    expect(store.priorityToolsError).toContain('NOT_FOUND')
    expect(store.priorityToolsError).toContain('工具目录暂不可用')
  })

  it('rejects a success envelope instead of guessing that a bare catalog is wrapped', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ tools: [{ description: 'stale', name: 'create_prediction', parameters: {}, permission: 'assistant:run', requires_confirmation: false, schema_version: '1.0' }] }))
    const store = useAssistantStore()
    store.priorityTools = [{ description: 'stale', name: 'create_prediction', parameters: {}, permission: 'assistant:run', requires_confirmation: false, schema_version: '1.0' }]

    await expect(store.listPriorityTools()).resolves.toEqual([])

    expect(store.priorityToolsState).toBe('error')
    expect(store.priorityTools).toEqual([])
    expect(store.priorityToolsError).toBe('能力目录请求失败')
  })

  it('loads summary filters and calls only the explicit project-scoped GET endpoints', async () => {
    const requests: { url: string; method: string }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      requests.push({ url, method: init?.method ?? 'GET' })
      if (url.endsWith('/data-domains')) return jsonResponse([{ id: 'domain-tlc', chromatography_type: 'tlc', version: 'v1', record_schema: {}, identity_fields: [], supported_import_modes: [], unit_definitions: {}, deduplication_policy: 'reject' }])
      if (url.endsWith('/prediction/types')) return jsonResponse([{ id: 'tlc', display_name: 'TLC', health: { status: 'READY' }, tools: ['single'] }])
      if (url.includes('/data-version-summary')) return jsonResponse({ project_id: 'project-1', domain_id: 'domain-tlc', count: 0, source: { source_type: 'data_version', source_ids: [], version: null } })
      if (url.includes('/prediction-record-summary')) return jsonResponse({ project_id: 'project-1', chromatography_type: 'tlc', count: 1, source: { source_type: 'prediction_record', source_ids: ['record-1'], version: null } })
      throw new Error(`Unexpected assistant summary request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.accessContext = accessContext('project-1')

    const domains = await store.listDataDomains()
    const types = await store.listPredictionTypes()
    const versions = await store.getDataVersionSummary('domain-tlc')
    const records = await store.getPredictionRecordSummary('tlc')

    expect(domains.data[0]?.id).toBe('domain-tlc')
    expect(types[0]?.id).toBe('tlc')
    expect(versions.source.source_type).toBe('data_version')
    expect(records.source.source_type).toBe('prediction_record')
    expect(requests).toEqual([
      { url: '/api/v1/data-domains', method: 'GET' },
      { url: '/api/v1/prediction/types', method: 'GET' },
      { url: '/api/v1/assistant/priority/data-version-summary?project_id=project-1&domain_id=domain-tlc', method: 'GET' },
      { url: '/api/v1/assistant/priority/prediction-record-summary?project_id=project-1&chromatography_type=tlc', method: 'GET' },
    ])
  })

  it('fails closed for project summaries when the project access scope is unresolved', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'needs_selection' } as never

    await expect(store.getDataVersionSummary('domain-tlc')).rejects.toThrow('访问范围未解析')
    await expect(store.getPredictionRecordSummary()).rejects.toThrow('访问范围未解析')
    expect(fetch).not.toHaveBeenCalled()
  })


  it('exposes only public normalized structure fields and warnings', () => {
    expect(structureToolSummary({ structure_ref: { id: 'structure-1', path: '/srv/private' }, canonical_smiles: 'C[C@H](O)F', warnings: [{ code: 'STEREO', message: '立体信息已保留' }], stack: 'secret' })).toEqual({ structureRef: 'structure-1', canonicalSmiles: 'C[C@H](O)F', stereochemistryStatus: null, hasStereochemistry: null, warnings: ['立体信息已保留'] })
    expect(structureToolSummary({ structure_ref: 'structure-2', warnings: [{ path: '/srv/private' }, { message: 'Traceback: /srv/private/secret' }, { message: '公开提示' }] })).toEqual({ structureRef: 'structure-2', canonicalSmiles: null, stereochemistryStatus: null, hasStereochemistry: null, warnings: ['公开提示'] })
    expect(structureToolSummary({ structure_ref: '/srv/private/structure-3' })?.structureRef).toBeNull()
  })

  it('builds confirmation and recovery requests without exposing IDs in a form', () => {
    expect(makeResumeBody('recovery')).toEqual({ reason: 'recovery' })
    expect(makeResumeBody('confirmation', 'confirmation-server-value')).toEqual({ reason: 'confirmation', confirmation_id: 'confirmation-server-value' })
    expect(() => makeResumeBody('confirmation')).toThrow('confirmation resume requires confirmation_id')
  })

  it('accepts only successful registration create/get calls with a safe matching resource reference', () => {
    const valid = { tool_name: 'create_registration_draft', status: 'SUCCEEDED', resource_ref: { type: 'model_registration', id: 'registration-1' } }
    expect(assistantRegistrationDraftResourceId(valid as never)).toBe('registration-1')
    expect(assistantRegistrationDraftResourceId({ ...valid, tool_name: 'get_registration_draft' } as never)).toBe('registration-1')
    expect(assistantRegistrationDraftResourceId({ ...valid, status: 'FAILED' } as never)).toBeNull()
    expect(assistantRegistrationDraftResourceId({ ...valid, tool_name: 'list_models' } as never)).toBeNull()
    expect(assistantRegistrationDraftResourceId({ ...valid, resource_ref: { type: 'prediction', id: 'registration-1' } } as never)).toBeNull()
    expect(assistantRegistrationDraftResourceId({ ...valid, resource_ref: { type: 'model_registration', id: '../registration-1' } } as never)).toBeNull()
    expect(assistantRegistrationDraftResourceId({ ...valid, resource_ref: null } as never)).toBeNull()
  })

  it('accepts only successful prediction creation calls with a safe prediction resource reference', () => {
    const valid = { tool_name: 'create_prediction', status: 'SUCCEEDED', resource_ref: { type: 'prediction', id: 'prediction-1' } }
    expect(assistantPredictionResourceId(valid as never)).toBe('prediction-1')
    expect(assistantPredictionResourceId({ ...valid, status: 'FAILED' } as never)).toBeNull()
    expect(assistantPredictionResourceId({ ...valid, tool_name: 'get_prediction' } as never)).toBeNull()
    expect(assistantPredictionResourceId({ ...valid, resource_ref: { type: 'model_registration', id: 'prediction-1' } } as never)).toBeNull()
    expect(assistantPredictionResourceId({ ...valid, resource_ref: { type: 'prediction', id: '../prediction-1' } } as never)).toBeNull()
  })

  it('requeries a registration draft under the resolved current project and binds the response', async () => {
    const requests: string[] = []
    const draft = { id: 'registration-1', project_id: 'project-1', revision: 2, etag: 'draft-etag', config_hash: 'a'.repeat(64), created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z', name: 'Model A', status: 'DRAFT', task_type: 'tlc', source: { kind: 'external_package', artifact_id: 'artifact-1' } }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => { requests.push(String(input)); return jsonResponse(draft) })
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.accessContext = accessContext('project-1')

    const result = await store.getRegistrationDraft('registration-1')

    expect(requests).toEqual(['/api/v1/assistant/priority/model-registration-drafts/registration-1?project_id=project-1'])
    expect(result.id).toBe('registration-1')
    expect(store.registrationDrafts['registration-1']).toMatchObject({ status: 'DRAFT', revision: 2 })
    expect(store.registrationDraftStatus['registration-1']).toBe('ready')
    expect(assistantRegistrationDraftPath('registration-1', 'project-1')).toContain('?project_id=project-1')
  })

  it.each([
    ['different registration id', { id: 'registration-2', project_id: 'project-1' }],
    ['different project id', { id: 'registration-1', project_id: 'project-2' }],
  ])('rejects a response with a %s and never stores it as a successful draft', async (_label, identity) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({
      ...identity,
      revision: 1,
      etag: 'private-etag',
      config_hash: 'a'.repeat(64),
      created_at: '2026-10-01T00:00:00Z',
      updated_at: '2026-10-01T00:00:00Z',
      name: 'Wrong project draft',
      status: 'DRAFT',
      task_type: 'tlc',
      source: { kind: 'external_package', artifact_id: 'private-artifact' },
    }))
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.accessContext = accessContext('project-1')

    await expect(store.getRegistrationDraft('registration-1')).rejects.toThrow('服务端返回的登记草稿与当前项目引用不匹配')

    expect(store.registrationDrafts['registration-1']).toBeUndefined()
    expect(store.registrationDraftStatus['registration-1']).toBe('error')
    expect(store.registrationDraftErrors['registration-1']).toBe('服务端返回的登记草稿与当前项目引用不匹配')
  })

  it('clears a prior draft and retains the public HTTP error when requery fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: '登记草稿不存在' } }), { status: 404, headers: { 'content-type': 'application/json' } }))
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.accessContext = accessContext('project-1')
    store.registrationDrafts = { 'registration-1': { id: 'registration-1', project_id: 'project-1', revision: 1, etag: 'stale', config_hash: 'a'.repeat(64), created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', name: 'Old', status: 'DRAFT', task_type: 'tlc', source: { kind: 'external_package', artifact_id: 'artifact-1' } } as never }

    await expect(store.getRegistrationDraft('registration-1')).rejects.toThrow('登记草稿不存在')

    expect(store.registrationDrafts['registration-1']).toBeUndefined()
    expect(store.registrationDraftStatus['registration-1']).toBe('error')
    expect(store.registrationDraftErrors['registration-1']).toBe('NOT_FOUND: 登记草稿不存在')
  })

  it('clears a cached prediction before requery and rejects a failed GET without stale success', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: '预测不存在' } }), { status: 404, headers: { 'content-type': 'application/json' } }))
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.accessContext = accessContext('project-1'); store.selectedConversationId = 'conversation-1'
    store.predictionResults = { 'prediction-1': { id: 'prediction-1', project_id: 'project-1', status: 'SUCCEEDED', chromatography_type: 'tlc', result: { task_type: 'tlc', rows: [{ rf: 0.5 }] } } as never }

    await expect(store.getPredictionResult('prediction-1')).rejects.toThrow('预测不存在')

    expect(store.predictionResults['prediction-1']).toBeUndefined()
  })

  it('does not cache a late prediction response after the selected conversation changes', async () => {
    let resolveResponse!: (response: Response) => void
    vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise<Response>((resolve) => { resolveResponse = resolve }))
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.accessContext = accessContext('project-1'); store.selectedConversationId = 'conversation-1'
    const pending = store.getPredictionResult('prediction-1')
    store.selectedConversationId = 'conversation-2'
    resolveResponse(jsonResponse({ id: 'prediction-1', project_id: 'project-1', status: 'SUCCEEDED', chromatography_type: 'tlc', result: null }))

    await expect(pending).rejects.toThrow('服务端返回的预测结果与当前项目引用不匹配')
    expect(store.predictionResults['prediction-1']).toBeUndefined()
  })

  it.each([
    ['different prediction id', { id: 'prediction-2', project_id: 'project-1' }],
    ['different project id', { id: 'prediction-1', project_id: 'project-2' }],
  ])('rejects a prediction response with a %s and does not cache it', async (_label, identity) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse({ id: identity.id, project_id: identity.project_id, status: 'SUCCEEDED', chromatography_type: 'tlc', result: null }))
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.accessContext = accessContext('project-1'); store.selectedConversationId = 'conversation-1'

    await expect(store.getPredictionResult('prediction-1')).rejects.toThrow('服务端返回的预测结果与当前项目引用不匹配')
    expect(store.predictionResults['prediction-1']).toBeUndefined()
  })

  it('shows the public error code and summary from an existing failed run', () => {
    expect(assistantFailureNotice({ error: { code: 'PROVIDER_REQUEST_REJECTED', message: '模型服务拒绝了请求', retryable: false } })).toEqual({ code: 'PROVIDER_REQUEST_REJECTED', summary: '模型服务拒绝了请求' })
  })

  it('recovers the public failure from historical or live SSE events', () => {
    const event = { seq: '3', type: 'run.failed', schema_version: '1.0', timestamp: '2026-10-03T00:00:00Z', agent_run_id: 'run-1', data: { error: { code: 'PROVIDER_REQUEST_REJECTED', message: '模型服务拒绝了请求', retryable: false } } }
    expect(assistantFailureNotice({ error: null }, [event])).toEqual({ code: 'PROVIDER_REQUEST_REJECTED', summary: '模型服务拒绝了请求' })
    expect(assistantFailureNotice({ error: null }, [], [event])).toEqual({ code: 'PROVIDER_REQUEST_REJECTED', summary: '模型服务拒绝了请求' })
  })

  it('uses a safe public fallback when a failure has no summary', () => {
    expect(assistantFailureNotice({ error: { code: 'PROVIDER_REQUEST_REJECTED', message: '', retryable: false } })).toEqual({ code: 'PROVIDER_REQUEST_REJECTED', summary: '模型服务拒绝了请求' })
  })

  it('does not show historical errors on succeeded runs or tool calls', () => {
    expect(assistantFailureNotice({ status: 'SUCCEEDED', error: { code: 'INTERNAL_ERROR', message: 'stale', retryable: false } })).toBeNull()
    expect(assistantFailureNotice({ status: 'FAILED', error: { code: 'PROVIDER_REQUEST_REJECTED', message: '公开失败', retryable: false } })).toMatchObject({ code: 'PROVIDER_REQUEST_REJECTED' })
    expect(toolResultSummaryEntries({ status: 'SUCCEEDED', result_summary: { error: 'stale', visible: 'ok' } })).toEqual([['visible', 'ok']])
    expect(toolResultSummaryEntries({ status: 'FAILED', result_summary: { error: '公开失败' } })).toEqual([['error', '公开失败']])
  })

  it('gates sending on project context, current conversation, input, and run state', () => {
    const base = { projectId: 'project-1', projectSelection: 'resolved', conversationId: 'conversation-1', draft: '查询预测', runStatus: null }
    expect(canSendAssistantMessage(base)).toBe(true)
    expect(canSendAssistantMessage({ ...base, projectId: null })).toBe(false)
    expect(canSendAssistantMessage({ ...base, projectSelection: 'needs_selection' })).toBe(false)
    expect(canSendAssistantMessage({ ...base, conversationId: null })).toBe(false)
    expect(canSendAssistantMessage({ ...base, draft: '   ' })).toBe(false)
    expect(canSendAssistantMessage({ ...base, runStatus: 'RUNNING' })).toBe(false)
    expect(canSendAssistantMessage({ ...base, runStatus: 'SUCCEEDED' })).toBe(true)
  })

  it('blocks concurrent message submissions and releases the lock after a failed request', async () => {
    let releaseFirstPost!: (response: Response) => void
    let postCount = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.endsWith('/conversations/conversation-1/messages') && init?.method === 'POST') {
        postCount += 1
        if (postCount === 1) return new Promise<Response>((resolve) => { releaseFirstPost = resolve })
        return jsonResponse({ agent_run_id: 'run-1' }, 202)
      }
      if (url.endsWith('/conversations/conversation-1/messages')) return jsonResponse([])
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'SUCCEEDED', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/events')) return jsonResponse({ events: [], next_seq: null })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.selectedConversationId = 'conversation-1'
    store.conversations.push(conversation('conversation-1', 'project-1'))
    const body = { client_message_id: 'client-1', content: '查询预测', mode: 'business' as const }

    const first = store.sendMessage('conversation-1', body)
    await expect(store.sendMessage('conversation-1', { ...body, client_message_id: 'client-2' })).rejects.toThrow('消息正在提交')
    expect(postCount).toBe(1)

    releaseFirstPost(jsonResponse({ detail: 'temporarily unavailable' }, 503))
    await expect(first).rejects.toThrow()
    await store.sendMessage('conversation-1', { ...body, client_message_id: 'client-3' })
    expect(postCount).toBe(2)
  })

  it.each([
    ['knowledge', 'business'],
    ['business', 'knowledge'],
  ] as const)('does not forward caller mode %s for a fixed %s conversation', async (conversationMode, callerMode) => {
    let postBody: Record<string, unknown> | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url.endsWith('/conversations/conversation-1/messages') && init?.method === 'POST') {
        postBody = JSON.parse(String(init.body)) as Record<string, unknown>
        return jsonResponse({ agent_run_id: 'run-1' }, 202)
      }
      if (url.endsWith('/conversations/conversation-1/messages')) return jsonResponse([])
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'SUCCEEDED', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/events')) return jsonResponse({ events: [], next_seq: null })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.selectedConversationId = 'conversation-1'
    store.conversations = [conversation('conversation-1', 'project-1', null, conversationMode)]

    await store.sendMessage('conversation-1', { client_message_id: 'client-1', content: '继续当前会话', mode: callerMode })

    expect(postBody).toMatchObject({ client_message_id: 'client-1', content: '继续当前会话' })
    expect(postBody).not.toHaveProperty('mode')
  })

  it.each([
    ['conversation is not selected', (store: ReturnType<typeof useAssistantStore>) => { store.selectedConversationId = null; store.conversations = [conversation('conversation-1', 'project-1')] }],
    ['conversation mode is missing', (store: ReturnType<typeof useAssistantStore>) => { store.conversations = [{ ...conversation('conversation-1', 'project-1'), mode: undefined } as never] }],
    ['conversation mode is invalid', (store: ReturnType<typeof useAssistantStore>) => { store.conversations = [{ ...conversation('conversation-1', 'project-1'), mode: 'unknown' } as never] }],
    ['conversation belongs to another project', (store: ReturnType<typeof useAssistantStore>) => { store.conversations = [conversation('conversation-1', 'project-2')] }],
  ])('does not send when %s', async (_label, configureStore) => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]))
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.selectedConversationId = 'conversation-1'
    configureStore(store)

    await expect(store.sendMessage('conversation-1', { client_message_id: 'client-1', content: '不应提交' })).rejects.toThrow('当前会话不属于可用项目')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('does not send a message to an old conversation after selection changes', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.selectedConversationId = 'conversation-2'
    store.conversations = [conversation('conversation-1', 'project-1'), conversation('conversation-2', 'project-1')]

    await expect(store.sendMessage('conversation-1', { client_message_id: 'client-old', content: '旧会话' })).rejects.toThrow('当前会话不属于可用项目')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('closes the event stream on a terminal run event without reporting normal EOF as an error', async () => {
    const event = { seq: '1', type: 'run.completed', schema_version: '1.0', timestamp: '2026-10-03T00:00:00Z', agent_run_id: 'run-1', data: { status: 'SUCCEEDED' } }
    const requests: string[] = []
    const streamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(`id: 1\ndata: ${JSON.stringify(event)}\n\n`))
        controller.close()
      },
    })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      requests.push(url)
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response(streamBody, { headers: { 'content-type': 'text/event-stream' } })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'SUCCEEDED', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/messages')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as ContractAgentRun

    store.startEventStream('run-1')
    await vi.waitFor(() => expect(store.run?.status).toBe('SUCCEEDED'))
    await vi.waitFor(() => expect(requests).toContain('/api/v1/conversations/conversation-1/messages'))
    expect(store.liveEvents.map((item) => item.type)).toEqual(['run.completed'])
    expect(store.error).toBeNull()
    store.stopEventStream()
  })

  it('keeps Unicode delta text separate and follows an older details read with the completed-message snapshot', async () => {
    let streamController: ReadableStreamDefaultController<Uint8Array> | undefined
    let releaseOldMessages: ((response: Response) => void) | undefined
    let messageGets = 0
    const messageSnapshot = { id: 'message-1', conversation_id: 'conversation-1', agent_run_id: 'run-1', role: 'assistant', status: 'FINAL', seq: '3', blocks: [{ type: 'text', text: '中🙂' }], created_at: '2026-10-03T00:00:00Z' }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response(new ReadableStream({ start(controller) { streamController = controller } }), { headers: { 'content-type': 'text/event-stream' } })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/messages')) {
        messageGets += 1
        if (messageGets === 1) return new Promise<Response>((resolve) => { releaseOldMessages = resolve })
        return jsonResponse([messageSnapshot])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as ContractAgentRun
    const emit = (seq: string, type: string, data: Record<string, unknown>) => streamController?.enqueue(new TextEncoder().encode(`id: ${seq}\ndata: ${JSON.stringify({ seq, type, schema_version: '1.0', timestamp: '2026-10-03T00:00:00Z', agent_run_id: 'run-1', data })}\n\n`))

    store.startEventStream('run-1')
    await vi.waitFor(() => expect(streamController).toBeDefined())
    emit('1', 'tool.started', { tool_name: 'get_prediction' })
    await vi.waitFor(() => expect(messageGets).toBe(1))
    emit('2', 'message.delta', { message_id: 'message-1', block_index: 0, delta: '中🙂', offset: 0 })
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('中🙂'))
    expect(store.messages).toEqual([])
    emit('3', 'message.delta', { message_id: 'message-1', block_index: 0, delta: '缺口', offset: 7 })
    expect(store.streamTextProjections[0]?.text).toBe('中🙂')
    emit('4', 'message.completed', { message_id: 'message-1' })
    releaseOldMessages?.(jsonResponse([]))

    await vi.waitFor(() => expect(messageGets).toBe(2))
    await vi.waitFor(() => expect(store.messages[0]?.id).toBe('message-1'))
    expect(store.streamTextProjections).toEqual([])
    expect(store.messages[0]?.blocks?.[0]).toMatchObject({ type: 'text', text: '中🙂' })
    store.stopEventStream()
  })

  it('seeds a first nonzero offset from a PARTIAL GET and appends the next delta without another read', async () => {
    const source = assistantEventStream()
    let messageGets = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response(source.body, { headers: { 'content-type': 'text/event-stream' } })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/messages')) {
        messageGets += 1
        return jsonResponse([{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'PARTIAL', seq: '1', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: 'abc' }] }])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as ContractAgentRun
    store.startEventStream('run-1')
    await vi.waitFor(() => expect(source.ready).toBe(true))
    source.emit('1', 'message.delta', { message_id: 'message-1', block_index: 0, delta: 'c', offset: 2 })
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('abc'))
    source.emit('2', 'message.delta', { message_id: 'message-1', block_index: 0, delta: 'd', offset: 3 })
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('abcd'))
    expect(messageGets).toBe(1)
    store.stopEventStream()
  })

  it('replaces a projection from the warning snapshot and resumes at its new Unicode offset', async () => {
    const source = assistantEventStream()
    let messageGets = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response(source.body, { headers: { 'content-type': 'text/event-stream' } })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/messages')) {
        messageGets += 1
        return jsonResponse([{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'PARTIAL', seq: '2', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '新文本' }] }])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.selectedConversationId = 'conversation-1'
    store.messages = [{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'PARTIAL', seq: '1', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '中🙂' }] }] as never
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as ContractAgentRun
    store.startEventStream('run-1')
    await vi.waitFor(() => expect(source.ready).toBe(true))
    source.emit('1', 'message.delta', { message_id: 'message-1', block_index: 0, delta: 'x', offset: 2 })
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('中🙂x'))
    source.emit('2', 'warning', { code: 'MESSAGE_SNAPSHOT_REPLACED', message_id: 'message-1' })
    await vi.waitFor(() => expect(messageGets).toBe(1))
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('新文本'))
    source.emit('3', 'message.delta', { message_id: 'message-1', block_index: 0, delta: 'd', offset: 3 })
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('新文本d'))
    expect(messageGets).toBe(1)
    store.stopEventStream()
  })

  it('replaces a shorter projection with the longer FINAL snapshot on message.completed', async () => {
    const source = assistantEventStream()
    let messageGets = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response(source.body, { headers: { 'content-type': 'text/event-stream' } })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/messages')) {
        messageGets += 1
        return jsonResponse([{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'FINAL', seq: '2', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '半句补全' }] }])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as ContractAgentRun
    store.startEventStream('run-1')
    await vi.waitFor(() => expect(source.ready).toBe(true))
    source.emit('1', 'message.delta', { message_id: 'message-1', block_index: 0, delta: '半句', offset: 0 })
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('半句'))
    source.emit('2', 'message.completed', { message_id: 'message-1' })

    await vi.waitFor(() => expect(messageGets).toBe(1))
    await vi.waitFor(() => expect(store.messages[0]?.status).toBe('FINAL'))
    expect(store.messages[0]?.blocks?.[0]).toMatchObject({ type: 'text', text: '半句补全' })
    expect(store.streamTextProjections).toEqual([])
    store.stopEventStream()
  })

  it('lets a recovery snapshot replace conflicting projection text with a different FINAL message', async () => {
    const source = assistantEventStream()
    let messageGets = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response(source.body, { headers: { 'content-type': 'text/event-stream' } })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/messages')) {
        messageGets += 1
        return jsonResponse([{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'FINAL', seq: '2', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '权威最终文本' }] }])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as ContractAgentRun
    store.startEventStream('run-1')
    await vi.waitFor(() => expect(source.ready).toBe(true))
    source.emit('1', 'message.delta', { message_id: 'message-1', block_index: 0, delta: 'old', offset: 0 })
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('old'))
    source.emit('2', 'message.delta', { message_id: 'message-1', block_index: 0, delta: 'x', offset: 1 })
    await vi.waitFor(() => expect(messageGets).toBe(1))
    await vi.waitFor(() => expect(store.messages[0]?.status).toBe('FINAL'))
    expect(store.messages[0]?.blocks?.[0]).toMatchObject({ type: 'text', text: '权威最终文本' })
    expect(store.streamTextProjections).toEqual([])
    store.stopEventStream()
  })

  it('does not let a recovery GET overwrite a newer delta and performs a successor snapshot', async () => {
    const source = assistantEventStream()
    let messageGets = 0
    let releaseRecovery: ((response: Response) => void) | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response(source.body, { headers: { 'content-type': 'text/event-stream' } })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/messages')) {
        messageGets += 1
        if (messageGets === 1) return new Promise<Response>((resolve) => { releaseRecovery = resolve })
        return jsonResponse([{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'PARTIAL', seq: '4', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: 'abcd' }] }])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as ContractAgentRun
    store.startEventStream('run-1')
    await vi.waitFor(() => expect(source.ready).toBe(true))
    source.emit('1', 'message.delta', { message_id: 'message-1', block_index: 0, delta: 'abc', offset: 0 })
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('abc'))
    source.emit('2', 'message.delta', { message_id: 'message-1', block_index: 0, delta: 'X', offset: 1 })
    await vi.waitFor(() => expect(messageGets).toBe(1))
    source.emit('3', 'message.delta', { message_id: 'message-1', block_index: 0, delta: 'd', offset: 3 })
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('abcd'))
    releaseRecovery?.(jsonResponse([{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'PARTIAL', seq: '3', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '过期快照' }] }]))

    await vi.waitFor(() => expect(messageGets).toBe(2))
    await vi.waitFor(() => expect(store.streamTextProjections[0]?.text).toBe('abcd'))
    store.stopEventStream()
  })

  it('coalesces ordinary run and tool progress into one refresh exactly at 100ms', async () => {
    vi.useFakeTimers()
    const source = assistantEventStream()
    const requests: { url: string; method: string }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response(source.body, { headers: { 'content-type': 'text/event-stream' } })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/messages')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as ContractAgentRun
    store.startEventStream('run-1')
    await flushAssistantMicrotasks()
    expect(source.ready).toBe(true)
    source.emit('1', 'run.progress', { stage: 'working' })
    source.emit('2', 'tool.started', { tool_name: 'get_prediction' })
    source.emit('3', 'tool.progress', { stage: 'reading' })
    await flushAssistantMicrotasks()
    expect(store.liveEvents).toHaveLength(3)

    const detailGets = () => requests.filter((request) => request.method === 'GET' && !request.url.endsWith('/events/stream'))
    expect(detailGets()).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(99)
    expect(detailGets()).toHaveLength(0)
    await vi.advanceTimersByTimeAsync(1)
    await flushAssistantMicrotasks()
    expect(detailGets()).toHaveLength(3)
    expect(detailGets().map((request) => request.url).sort()).toEqual([
      '/api/v1/agent-runs/run-1',
      '/api/v1/agent-runs/run-1/tool-calls',
      '/api/v1/conversations/conversation-1/messages',
    ])
    store.stopEventStream()
    vi.useRealTimers()
  })

  it('serializes urgent completion and replacement behind an in-flight progress refresh', async () => {
    vi.useFakeTimers()
    const source = assistantEventStream()
    const requests: { url: string; method: string }[] = []
    let messageGets = 0
    let releaseOldMessages: ((response: Response) => void) | undefined
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response(source.body, { headers: { 'content-type': 'text/event-stream' } })
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/messages')) {
        messageGets += 1
        if (messageGets === 1) return new Promise<Response>((resolve) => { releaseOldMessages = resolve })
        return jsonResponse([{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'FINAL', seq: '5', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '最终完整文本' }] }])
      }
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as ContractAgentRun
    store.startEventStream('run-1')
    await flushAssistantMicrotasks()
    expect(source.ready).toBe(true)
    source.emit('1', 'message.delta', { message_id: 'message-1', block_index: 0, delta: '半句', offset: 0 })
    await flushAssistantMicrotasks()
    expect(store.streamTextProjections[0]?.text).toBe('半句')
    expect(messageGets).toBe(0)
    source.emit('2', 'tool.started', { tool_name: 'get_prediction' })
    await flushAssistantMicrotasks()
    expect(store.liveEvents).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(100)
    await flushAssistantMicrotasks()
    expect(messageGets).toBe(1)
    const detailGets = () => requests.filter((request) => request.method === 'GET' && !request.url.endsWith('/events/stream'))
    expect(detailGets()).toHaveLength(3)

    source.emit('3', 'message.completed', { message_id: 'message-1' })
    source.emit('4', 'warning', { code: 'MESSAGE_SNAPSHOT_REPLACED', message_id: 'message-1' })
    await flushAssistantMicrotasks()
    expect(store.liveEvents).toHaveLength(4)
    expect(detailGets()).toHaveLength(3)
    releaseOldMessages?.(jsonResponse([{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'PARTIAL', seq: '2', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '旧快照' }] }]))

    await flushAssistantMicrotasks()
    expect(messageGets).toBe(2)
    expect(store.messages[0]?.status).toBe('FINAL')
    expect(detailGets()).toHaveLength(6)
    expect(store.messages[0]?.blocks?.[0]).toMatchObject({ type: 'text', text: '最终完整文本' })
    expect(store.streamTextProjections).toEqual([])
    store.stopEventStream()
    vi.useRealTimers()
  })

  it('validates a server-provided confirmation ID before resuming', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      requests.push({ url: String(input), init })
      return requests.length === 1 ? jsonResponse(confirmation()) : jsonResponse({ id: 'run-1' }, 202, { Location: '/api/v1/agent-runs/run-1' })
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'
    await store.confirmRun('run-1', binding('confirmation-1'), '"run-etag"')
    expect(requests.map((request) => request.url)).toEqual(['/api/v1/confirmations/confirmation-1', '/api/v1/agent-runs/run-1/resume'])
    expect(requestBody(requests[1])).toEqual({ reason: 'confirmation', confirmation_id: 'confirmation-1' })
  })

  it.each([
    ['tampered resource binding', { resource_ref: { type: 'model_registration', id: 'other-resource' } }],
    ['expired confirmation', { expires_at: '2020-01-01T00:00:00Z' }],
    ['cross-project confirmation', { project_id: 'other-project' }],
  ])('rejects %s before resume', async (_label, overrides) => {
    const requests: Array<{ url: string; init?: RequestInit }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      requests.push({ url: String(input), init })
      return jsonResponse(confirmation(overrides))
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'
    await expect(store.confirmRun('run-1', binding('confirmation-1'), '"run-etag"')).rejects.toThrow('确认对象')
    expect(requests).toHaveLength(1)
  })

  it('recovers a consumed confirmation without resuming or repeating the side effect', async () => {
    const requests: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input); requests.push(url)
      if (url.endsWith('/confirmations/confirmation-1')) return jsonResponse(confirmation({ status: 'CONSUMED', consumed_at: '2026-01-01T00:01:00Z' }))
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'SUCCEEDED', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/events')) return jsonResponse({ events: [], next_seq: null })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([{ id: 'tool-1', tool_name: 'create_registration_draft', status: 'SUCCEEDED' }])
      if (url.endsWith('/conversations/conversation-1/messages')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'
    await store.confirmRun('run-1', binding('confirmation-1'), '"run-etag"')
    expect(requests).not.toContain('/api/v1/agent-runs/run-1/resume')
    expect(requests).toContain('/api/v1/agent-runs/run-1/tool-calls')
    expect(requests).toContain('/api/v1/conversations/conversation-1/messages')
    expect(store.run?.status).toBe('SUCCEEDED')
    expect(store.confirmationNotice).toBe('已完成/已恢复')
  })

  it('creates confirmation through the formal preview/create API when binding has no ID', async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      requests.push({ url: String(input), init })
      if (requests.length === 1) return jsonResponse({ action: 'create_registration_draft', can_confirm: true, config_hash: 'a'.repeat(64), dependencies: [], etag: 'preview-etag', impact: {}, policy_version: 'policy-1', resource_ref: { type: 'model_registration', id: 'resource-1' }, revision: 1, summary: [] })
      if (requests.length === 2) return jsonResponse({ id: 'confirmation-created' }, 201, { ETag: 'confirmation-etag' })
      return jsonResponse({ id: 'run-1' }, 202, { Location: '/api/v1/agent-runs/run-1' })
    })
    const store = useAssistantStore()
    await store.confirmRun('run-1', binding(), '"run-etag"')
    expect(requests.map((request) => request.url)).toEqual(['/api/v1/confirmations/previews', '/api/v1/confirmations', '/api/v1/agent-runs/run-1/resume'])
    expect(requestBody(requests[0])).toMatchObject({ action: 'create_registration_draft', resource_ref: { id: 'resource-1' } })
    expect(requestBody(requests[2])).toEqual({ reason: 'confirmation', confirmation_id: 'confirmation-created' })
  })

  it('refreshes after response loss from conversation runs and latest tool calls', async () => {
    const requests: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input); requests.push(url)
      if (url.endsWith('/conversations/conversation-1/messages')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-1/runs')) return jsonResponse([{ id: 'run-2' }])
      if (url.endsWith('/agent-runs/run-2')) return jsonResponse({ id: 'run-2', conversation_id: 'conversation-1', status: 'SUCCEEDED', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-2/events')) return jsonResponse({ events: [], next_seq: null })
      if (url.endsWith('/agent-runs/run-2/tool-calls')) return jsonResponse([{ id: 'tool-2', tool_name: 'get_model_card', status: 'SUCCEEDED' }])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.conversations.push({ id: 'conversation-1', project_id: 'project-1', mode: 'business', title: '业务会话', owner_id: 'actor-1', status: 'ACTIVE', revision: 1, etag: 'conversation-etag', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', last_message_at: null, current_run_id: null })
    await store.loadConversation('conversation-1')
    expect(store.run?.id).toBe('run-2')
    expect(store.toolCalls).toHaveLength(1)
    expect(requests).toContain('/api/v1/conversations/conversation-1/runs')
    expect(requests).toContain('/api/v1/agent-runs/run-2/tool-calls')
  })

  it('preserves a still-valid conversation during same-project refresh', async () => {
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.selectedConversationId = 'conversation-1'; store.messages = [{ id: 'message-1' } as never]; store.run = { id: 'run-1' } as ContractAgentRun
    localStorage.setItem('chrom-platform.assistant', JSON.stringify({ projectId: 'project-1', conversationId: 'conversation-1' }))
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([conversation('conversation-1', 'project-1')]))
    await store.listConversations()
    expect(store.selectedConversationId).toBe('conversation-1')
    expect(store.messages).toHaveLength(1)
    expect(store.run?.id).toBe('run-1')
  })

  it('clears conversation state when the access project changes', async () => {
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.selectedConversationId = 'conversation-1'; store.messages = [{ id: 'message-1' } as never]; store.run = { id: 'run-1' } as ContractAgentRun; store.toolCalls = [{ id: 'tool-1' } as never]
    localStorage.setItem('chrom-platform.assistant', JSON.stringify({ projectId: 'project-1', conversationId: 'conversation-1' }))
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(accessContext('project-2')))
    await store.getAccessContext()
    expect(store.projectId).toBe('project-2')
    expect(store.selectedConversationId).toBeNull()
    expect(store.messages).toEqual([])
    expect(store.run).toBeNull()
    expect(store.toolCalls).toEqual([])
    expect(JSON.parse(localStorage.getItem('chrom-platform.assistant') ?? '{}')).toMatchObject({ projectId: 'project-2', conversationId: null })
  })

  it('keeps the existing conversation list unselected when bootstrap has an invalid persisted ID', async () => {
    localStorage.setItem('chrom-platform.assistant', JSON.stringify({ projectId: 'project-1', conversationId: 'stale-conversation' }))
    const requests: Array<{ url: string; method: string }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/system/access-context')) return jsonResponse(accessContext('project-1'))
      if (url.endsWith('/assistant/priority/tools')) return rawJsonResponse({ request_id: 'assistant-test', tools: [] })
      if (url.includes('/conversations?project_id=project-1')) return jsonResponse([conversation('conversation-1', 'project-1')])
      if (url.endsWith('/conversations')) return jsonResponse(conversation('unexpected-new', 'project-1'), 201)
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()

    await store.bootstrap()

    expect(store.conversations.map((item) => item.id)).toEqual(['conversation-1'])
    expect(store.selectedConversationId).toBeNull()
    expect(store.messages).toEqual([])
    expect(store.run).toBeNull()
    expect(requests.every(({ method }) => method === 'GET')).toBe(true)
    expect(JSON.parse(localStorage.getItem('chrom-platform.assistant') ?? '{}')).toMatchObject({ projectId: 'project-1', conversationId: null })
  })

  it('keeps a new user unselected on bootstrap and creates only after the explicit start action', async () => {
    const requests: Array<{ url: string; method?: string; body?: Record<string, unknown> }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); requests.push({ url, method: init?.method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url.endsWith('/system/access-context')) return jsonResponse(accessContext('project-1'))
      if (url.endsWith('/assistant/priority/tools')) return rawJsonResponse({ request_id: 'assistant-test', tools: [{ description: 'single prediction', name: 'create_prediction', parameters: {}, permission: 'assistant:run', requires_confirmation: false, schema_version: '1.0' }] })
      if (url.includes('/conversations?project_id=project-1')) return jsonResponse([conversation('existing-conversation', 'project-1', 'old-run')])
      if (url.endsWith('/conversations')) return jsonResponse(conversation('fresh-conversation', 'project-1'), 201)
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    await store.bootstrap()
    expect(store.conversations.map((item) => item.id)).toEqual(['existing-conversation'])
    expect(store.selectedConversationId).toBeNull()
    expect(store.run).toBeNull()
    expect(requests.some((item) => item.url === '/api/v1/assistant/priority/tools')).toBe(true)
    expect(store.priorityTools.map((tool) => tool.name)).toEqual(['create_prediction'])
    expect(requests.some((item) => item.method === 'POST')).toBe(false)

    await store.createConversation()

    expect(store.selectedConversationId).toBe('fresh-conversation')
    expect(requests.filter((item) => item.url.endsWith('/conversations') && item.method === 'POST')).toHaveLength(1)
    expect(requests.at(-1)?.body).toMatchObject({ project_id: 'project-1', mode: 'business' })
  })

  it('loads an existing listed conversation by GET after the user selects it', async () => {
    const requests: Array<{ url: string; method: string }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/system/access-context')) return jsonResponse(accessContext('project-1'))
      if (url.endsWith('/assistant/priority/tools')) return rawJsonResponse({ request_id: 'assistant-test', tools: [] })
      if (url.includes('/conversations?project_id=project-1')) return jsonResponse([conversation('existing-conversation', 'project-1', 'run-existing')])
      if (url.endsWith('/conversations/existing-conversation/messages')) return jsonResponse([{ id: 'existing-message' }])
      if (url.endsWith('/agent-runs/run-existing')) return jsonResponse({ id: 'run-existing', conversation_id: 'existing-conversation', status: 'SUCCEEDED', etag: 'existing-etag' })
      if (url.endsWith('/agent-runs/run-existing/events')) return jsonResponse({ events: [], next_seq: null, has_more: false, stream_status: 'CLOSED' })
      if (url.endsWith('/agent-runs/run-existing/tool-calls')) return jsonResponse([{ id: 'existing-tool' }])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()

    await store.bootstrap()
    expect(store.conversations.map((item) => item.id)).toEqual(['existing-conversation'])
    expect(store.selectedConversationId).toBeNull()

    await store.loadConversation('existing-conversation')

    expect(store.selectedConversationId).toBe('existing-conversation')
    expect(store.messages).toEqual([{ id: 'existing-message' }])
    expect(store.run?.id).toBe('run-existing')
    expect(store.toolCalls).toEqual([{ id: 'existing-tool' }])
    expect(requests.some(({ url }) => url.endsWith('/conversations?project_id=project-1'))).toBe(true)
    expect(requests.some(({ url }) => url.endsWith('/conversations/existing-conversation/messages'))).toBe(true)
    expect(requests.every(({ method }) => method === 'GET')).toBe(true)
  })

  it('leaves an empty conversation list without creating a conversation during bootstrap', async () => {
    const requests: Array<{ url: string; method?: string }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); requests.push({ url, method: init?.method })
      if (url.endsWith('/system/access-context')) return jsonResponse(accessContext('project-1'))
      if (url.endsWith('/assistant/priority/tools')) return rawJsonResponse({ request_id: 'assistant-test', tools: [] })
      if (url.includes('/conversations?project_id=project-1')) return jsonResponse([])
      if (url.endsWith('/conversations')) return jsonResponse(conversation('unexpected-new', 'project-1'), 201)
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()

    await store.bootstrap()

    expect(store.conversations).toEqual([])
    expect(store.selectedConversationId).toBeNull()
    expect(requests.every(({ method }) => method !== 'POST')).toBe(true)
  })

  it('clears old terminal state when creating a new project conversation', async () => {
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.accessContext = accessContext('project-1'); store.selectedConversationId = 'old-conversation'; store.run = { id: 'old-run', status: 'SUCCEEDED' } as ContractAgentRun; store.messages = [{ id: 'old-message' } as never]; store.toolCalls = [{ id: 'old-tool' } as never]
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(conversation('fresh-conversation', 'project-1'), 201))
    await store.createConversation()
    expect(store.selectedConversationId).toBe('fresh-conversation')
    expect(store.run).toBeNull()
    expect(store.messages).toEqual([])
    expect(store.toolCalls).toEqual([])
  })

  it('surfaces a real conversation creation failure without selecting a fake ID', async () => {
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.accessContext = accessContext('project-1')
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: '服务端拒绝创建会话' }), { status: 503, headers: { 'content-type': 'application/json' } }))
    await expect(store.createConversation()).rejects.toThrow()
    expect(store.selectedConversationId).toBeNull()
  })

  it('converges bootstrap with a real timeout when access context never responds', async () => {
    vi.useFakeTimers()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
    }))
    const store = useAssistantStore()
    const pending = store.bootstrap()
    const failure = expect(pending).rejects.toThrow('访问上下文请求超时')
    await vi.advanceTimersByTimeAsync(10_000)
    await failure
    expect(store.state).toBe('error')
    vi.useRealTimers()
  })

  it('shares one access-context request across concurrent bootstrap callers', async () => {
    let calls = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => { calls += 1; return jsonResponse(accessContext('project-1')) })
    const store = useAssistantStore()
    await Promise.all([store.getAccessContext(), store.getAccessContext()])
    expect(calls).toBe(1)
  })

  it('restores a valid in-progress run after refresh', async () => {
    localStorage.setItem('chrom-platform.assistant', JSON.stringify({ projectId: 'project-1', conversationId: 'conversation-1' }))
    const requests: string[] = []
    const methods: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); requests.push(url)
      methods.push(init?.method ?? 'GET')
      if (url.endsWith('/system/access-context')) return jsonResponse(accessContext('project-1'))
      if (url.includes('/conversations?project_id=project-1')) return jsonResponse([conversation('conversation-1', 'project-1', 'run-1')])
      if (url.endsWith('/conversations/conversation-1/messages')) return jsonResponse([])
      if (url.endsWith('/agent-runs/run-1/events')) return jsonResponse({ events: [], next_seq: null })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([])
      if (url.endsWith('/agent-runs/run-1')) return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' })
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response('', { status: 200, headers: { 'content-type': 'text/event-stream' } })
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    await store.bootstrap()
    store.stopEventStream()
    expect(store.selectedConversationId).toBe('conversation-1')
    expect(store.run).toMatchObject({ id: 'run-1', status: 'RUNNING' })
    expect(requests).toContain('/api/v1/agent-runs/run-1/events')
    expect(requests).toContain('/api/v1/agent-runs/run-1/tool-calls')
    expect(methods.every((method) => method === 'GET')).toBe(true)
  })

  it('ignores a late conversation load across A to B to A selection generations', async () => {
    let resolveFirstA!: (response: Response) => void
    const requests: string[] = []
    let aMessages = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input); requests.push(url)
      if (url.endsWith('/conversations/conversation-a/messages')) {
        aMessages += 1
        if (aMessages === 1) return new Promise<Response>((resolve) => { resolveFirstA = resolve })
        return jsonResponse([{ id: 'message-a-new' }])
      }
      if (url.endsWith('/conversations/conversation-b/messages')) return jsonResponse([{ id: 'message-b' }])
      if (url.endsWith('/agent-runs/run-a-new')) return jsonResponse({ id: 'run-a-new', conversation_id: 'conversation-a', status: 'SUCCEEDED', etag: 'a-new-etag' })
      if (url.endsWith('/agent-runs/run-a-new/events')) return jsonResponse({ events: [{ seq: '2' }], next_seq: '2', has_more: false, stream_status: 'CLOSED' })
      if (url.endsWith('/agent-runs/run-a-new/tool-calls')) return jsonResponse([{ id: 'tool-a-new' }])
      if (url.endsWith('/conversations/conversation-b/runs')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.conversations = [conversation('conversation-a', 'project-1', 'run-a-old'), conversation('conversation-b', 'project-1')]

    const firstA = store.loadConversation('conversation-a')
    await store.loadConversation('conversation-b')
    store.conversations = [conversation('conversation-a', 'project-1', 'run-a-new'), conversation('conversation-b', 'project-1')]
    await store.loadConversation('conversation-a')
    resolveFirstA(jsonResponse([{ id: 'message-a-old' }]))
    await firstA

    expect(store.selectedConversationId).toBe('conversation-a')
    expect(store.messages).toEqual([{ id: 'message-a-new' }])
    expect(store.run?.id).toBe('run-a-new')
    expect(store.events?.events).toEqual([{ seq: '2' }])
    expect(store.toolCalls).toEqual([{ id: 'tool-a-new' }])
    expect(requests).not.toContain('/api/v1/agent-runs/run-a-old')
    expect(requests.some((url) => url.includes('/agent-runs/run-a-old/events/stream'))).toBe(false)
  })

  it('does not publish late run, event, or tool-call details after switching conversations', async () => {
    const resolvers = new Map<string, (response: Response) => void>()
    const requests: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input); requests.push(url)
      if (url.endsWith('/conversations/conversation-a/messages')) return jsonResponse([{ id: 'message-a' }])
      if (url.endsWith('/conversations/conversation-b/messages')) return jsonResponse([{ id: 'message-b' }])
      if (url.endsWith('/agent-runs/run-a')) return new Promise<Response>((resolve) => { resolvers.set('run', resolve) })
      if (url.endsWith('/agent-runs/run-a/events')) return new Promise<Response>((resolve) => { resolvers.set('events', resolve) })
      if (url.endsWith('/agent-runs/run-a/tool-calls')) return new Promise<Response>((resolve) => { resolvers.set('tools', resolve) })
      if (url.endsWith('/agent-runs/run-b')) return jsonResponse({ id: 'run-b', conversation_id: 'conversation-b', status: 'SUCCEEDED', etag: 'b-etag' })
      if (url.endsWith('/agent-runs/run-b/events')) return jsonResponse({ events: [{ seq: '4' }], next_seq: '4', has_more: false, stream_status: 'CLOSED' })
      if (url.endsWith('/agent-runs/run-b/tool-calls')) return jsonResponse([{ id: 'tool-b' }])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.conversations = [conversation('conversation-a', 'project-1', 'run-a'), conversation('conversation-b', 'project-1', 'run-b')]

    const oldLoad = store.loadConversation('conversation-a')
    await vi.waitFor(() => expect(resolvers.size).toBe(3))
    await store.loadConversation('conversation-b')
    resolvers.get('run')!(jsonResponse({ id: 'run-a', conversation_id: 'conversation-a', status: 'RUNNING', etag: 'a-etag' }))
    resolvers.get('events')!(jsonResponse({ events: [{ seq: '1' }], next_seq: '1', has_more: false, stream_status: 'OPEN' }))
    resolvers.get('tools')!(jsonResponse([{ id: 'tool-a' }]))
    await oldLoad

    expect(store.selectedConversationId).toBe('conversation-b')
    expect(store.messages).toEqual([{ id: 'message-b' }])
    expect(store.run?.id).toBe('run-b')
    expect(store.events?.events).toEqual([{ seq: '4' }])
    expect(store.toolCalls).toEqual([{ id: 'tool-b' }])
    expect(requests.some((url) => url.includes('/agent-runs/run-a/events/stream'))).toBe(false)
  })

  it.each(['send', 'resume', 'cancel'] as const)('does not publish a late %s response after switching conversations', async (action) => {
    let resolveAction!: (response: Response) => void
    const requests: Array<{ url: string; method: string }> = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (action === 'send' && url.endsWith('/conversations/conversation-a/messages') && method === 'POST') return new Promise<Response>((resolve) => { resolveAction = resolve })
      if (action === 'resume' && url.endsWith('/agent-runs/run-a/resume')) return new Promise<Response>((resolve) => { resolveAction = resolve })
      if (action === 'cancel' && url.endsWith('/agent-runs/run-a/cancel')) return new Promise<Response>((resolve) => { resolveAction = resolve })
      if (url.endsWith('/conversations/conversation-b/messages')) return jsonResponse([{ id: 'message-b' }])
      if (url.endsWith('/conversations/conversation-b/runs')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.selectedConversationId = 'conversation-a'
    store.conversations = [conversation('conversation-a', 'project-1'), conversation('conversation-b', 'project-1')]
    if (action !== 'send') store.run = { id: 'run-a', conversation_id: 'conversation-a', status: 'RUNNING', etag: 'run-etag' } as ContractAgentRun

    const pending = action === 'send'
      ? store.sendMessage('conversation-a', { client_message_id: 'client-late', content: '迟到响应', mode: 'business' })
      : action === 'resume'
        ? store.resumeRun('run-a', makeResumeBody('recovery'), 'run-etag')
        : store.cancelRun('run-a', 'run-etag')
    await vi.waitFor(() => expect(resolveAction).toBeTypeOf('function'))
    await store.loadConversation('conversation-b')
    resolveAction(action === 'send'
      ? jsonResponse({ message_id: 'message-a', agent_run_id: 'run-a', status: 'QUEUED', events_url: '/events', stream_url: '/stream' }, 202)
      : jsonResponse({ id: 'run-a', conversation_id: 'conversation-a', status: 'RUNNING', etag: 'run-etag' }, 202))
    await pending

    expect(store.selectedConversationId).toBe('conversation-b')
    expect(store.messages).toEqual([{ id: 'message-b' }])
    expect(store.run).toBeNull()
    expect(requests.some(({ url }) => url.includes('/agent-runs/run-a/events/stream'))).toBe(false)
    expect(requests.some(({ url }) => url.endsWith('/agent-runs/run-a'))).toBe(false)
  })

  it('recovers an expired SSE cursor with GET-only detail polling until the run is terminal', async () => {
    vi.useFakeTimers()
    const requests: Array<{ url: string; method: string }> = []
    let runReads = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); const method = init?.method ?? 'GET'; requests.push({ url, method })
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response('', { status: 410 })
      if (url.endsWith('/agent-runs/run-1')) {
        runReads += 1
        return jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: runReads === 1 ? 'RUNNING' : 'SUCCEEDED', etag: 'run-etag' })
      }
      if (url.endsWith('/conversations/conversation-1/messages')) return jsonResponse([{ id: `message-${runReads}` }])
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([{ id: `tool-${runReads}` }])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' } as ContractAgentRun

    store.startEventStream('run-1')
    await vi.advanceTimersByTimeAsync(0)
    expect(store.run?.status).toBe('RUNNING')
    expect(runReads).toBe(1)
    expect(requests.some(({ url }) => url.endsWith('/agent-runs/run-1/events'))).toBe(false)

    await vi.advanceTimersByTimeAsync(2000)
    expect(store.run?.status).toBe('SUCCEEDED')
    expect(runReads).toBe(2)
    const countAtTerminal = requests.length
    await vi.advanceTimersByTimeAsync(6000)

    expect(requests).toHaveLength(countAtTerminal)
    expect(requests.every(({ method }) => method === 'GET')).toBe(true)
    expect(requests.filter(({ url }) => url.endsWith('/conversations/conversation-1/messages'))).toHaveLength(2)
    expect(requests.filter(({ url }) => url.endsWith('/agent-runs/run-1/tool-calls'))).toHaveLength(2)
    store.stopEventStream()
    vi.useRealTimers()
  })

  it('stops expired-cursor polling and ignores a late GET response after stop', async () => {
    vi.useFakeTimers()
    let resolveRun!: (response: Response) => void
    const requests: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input); requests.push(url)
      if (url.endsWith('/agent-runs/run-1/events/stream')) return new Response('', { status: 410 })
      if (url.endsWith('/agent-runs/run-1')) return new Promise<Response>((resolve) => { resolveRun = resolve })
      if (url.endsWith('/conversations/conversation-1/messages')) return jsonResponse([{ id: 'message-1' }])
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return jsonResponse([{ id: 'tool-1' }])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' } as ContractAgentRun

    store.startEventStream('run-1')
    await vi.waitFor(() => expect(resolveRun).toBeTypeOf('function'))
    store.stopEventStream()
    resolveRun(jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' }))
    await vi.advanceTimersByTimeAsync(0)
    const countAfterStop = requests.length
    await vi.advanceTimersByTimeAsync(6000)

    expect(requests).toHaveLength(countAfterStop)
    expect(store.run?.status).toBe('RUNNING')
    expect(requests.every((url) => !url.endsWith('/agent-runs/run-1/events'))).toBe(true)
    expect(requests.some((url) => url.includes('/agent-runs/run-1/events/stream'))).toBe(true)
    store.stopEventStream()
    vi.useRealTimers()
  })

  it('does not start SSE when a Run detail load completes after stream shutdown', async () => {
    const resolvers = new Map<string, (response: Response) => void>()
    const requests: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input); requests.push(url)
      if (url.endsWith('/agent-runs/run-1')) return new Promise<Response>((resolve) => { resolvers.set('run', resolve) })
      if (url.endsWith('/agent-runs/run-1/events')) return new Promise<Response>((resolve) => { resolvers.set('events', resolve) })
      if (url.endsWith('/agent-runs/run-1/tool-calls')) return new Promise<Response>((resolve) => { resolvers.set('tools', resolve) })
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.selectedConversationId = 'conversation-1'
    const pending = store.loadRun('run-1')
    await vi.waitFor(() => expect(resolvers.size).toBe(3))
    store.stopEventStream()
    resolvers.get('run')!(jsonResponse({ id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'run-etag' }))
    resolvers.get('events')!(jsonResponse({ events: [], next_seq: null, has_more: false, stream_status: 'OPEN' }))
    resolvers.get('tools')!(jsonResponse([]))
    await pending

    expect(store.run).toBeNull()
    expect(requests.some((url) => url.includes('/agent-runs/run-1/events/stream'))).toBe(false)
  })

  it('resets event cursors when loading another Run in the same conversation', async () => {
    const requests: Array<{ url: string; headers: Headers }> = []
    const event = (seq: string, runId: string) => ({ seq, type: 'assistant.notice', schema_version: '1.0', timestamp: '2026-10-03T00:00:00Z', agent_run_id: runId, data: {} })
    const eventStream = (events: unknown[]) => new Response(new ReadableStream<Uint8Array>({
      start(controller) {
        for (const item of events) controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(item)}\n\n`))
      },
    }), { headers: { 'content-type': 'text/event-stream' } })
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input); requests.push({ url, headers: new Headers(init?.headers) })
      if (url.endsWith('/agent-runs/run-a/events/stream')) return eventStream([event('1', 'run-a'), event('18', 'run-a')])
      if (url.endsWith('/agent-runs/run-b/events/stream')) return eventStream([event('1', 'run-b')])
      if (url.endsWith('/agent-runs/run-b')) return jsonResponse({ id: 'run-b', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'b-etag' })
      if (url.endsWith('/agent-runs/run-b/events')) return jsonResponse({ events: [], next_seq: null, has_more: false, stream_status: 'OPEN' })
      if (url.endsWith('/agent-runs/run-b/tool-calls')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.selectedConversationId = 'conversation-1'
    store.run = { id: 'run-a', conversation_id: 'conversation-1', status: 'RUNNING', etag: 'a-etag' } as ContractAgentRun

    store.startEventStream('run-a')
    await vi.waitFor(() => expect(store.lastEventSeq).toBe('18'))
    await store.loadRun('run-b')
    await vi.waitFor(() => expect(store.liveEvents.some((item) => item.agent_run_id === 'run-b')).toBe(true))
    const runBRequest = requests.find(({ url }) => url.includes('/agent-runs/run-b/events/stream'))

    expect(runBRequest?.url).not.toContain('after_seq=18')
    expect(runBRequest?.headers.has('Last-Event-ID')).toBe(false)
    expect(store.liveEvents.map((item) => [item.agent_run_id, item.seq])).toEqual([['run-b', '1']])
    expect(store.lastEventSeq).toBe('1')
    store.stopEventStream()
  })

  it('does not publish a consumed-confirmation notice after its conversation changes during Run recovery', async () => {
    let resolveRun!: (response: Response) => void
    const requests: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input); requests.push(url)
      if (url.endsWith('/confirmations/confirmation-1')) return jsonResponse(confirmation({ status: 'CONSUMED', consumed_at: '2026-10-03T00:00:00Z' }))
      if (url.endsWith('/agent-runs/run-a')) return new Promise<Response>((resolve) => { resolveRun = resolve })
      if (url.endsWith('/agent-runs/run-a/events')) return jsonResponse({ events: [], next_seq: null, has_more: false, stream_status: 'CLOSED' })
      if (url.endsWith('/agent-runs/run-a/tool-calls')) return jsonResponse([])
      if (url.endsWith('/conversations/conversation-b/messages')) return jsonResponse([{ id: 'message-b' }])
      if (url.endsWith('/conversations/conversation-b/runs')) return jsonResponse([])
      throw new Error(`unexpected request: ${url}`)
    })
    const store = useAssistantStore()
    store.projectId = 'project-1'; store.selectedConversationId = 'conversation-a'
    store.conversations = [conversation('conversation-a', 'project-1', 'run-a'), conversation('conversation-b', 'project-1')]
    store.run = { id: 'run-a', conversation_id: 'conversation-a', status: 'RUNNING', etag: 'a-etag' } as ContractAgentRun

    const pending = store.confirmRun('run-a', binding('confirmation-1'), 'a-etag')
    await vi.waitFor(() => expect(resolveRun).toBeTypeOf('function'))
    await store.loadConversation('conversation-b')
    resolveRun(jsonResponse({ id: 'run-a', conversation_id: 'conversation-a', status: 'SUCCEEDED', etag: 'a-etag' }))
    await pending

    expect(store.selectedConversationId).toBe('conversation-b')
    expect(store.messages).toEqual([{ id: 'message-b' }])
    expect(requests.filter((url) => url.endsWith('/conversations/conversation-b/messages'))).toHaveLength(1)
    expect(store.confirmationNotice).toBeNull()
    expect(store.confirmationNoticeRunId).toBeNull()
  })
})
