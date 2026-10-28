import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AssistantView from '@/modules/assistant/AssistantView.vue'
import { useAssistantStore } from '@/modules/assistant/store'
import { useConfirmationStore } from '@/stores/confirmations'
import { ApiClientError } from '@/core/api/client'

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, request_id: 'assistant-view-test', warnings: [] }), { status, headers: { 'content-type': 'application/json' } })
}

function mountAssistantView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAssistantStore()
  vi.spyOn(store, 'bootstrap').mockResolvedValue(undefined)
  const wrapper = mount(AssistantView, { global: { plugins: [pinia] } })
  return { store, wrapper }
}

function tlcResult(overrides: Record<string, unknown> = {}) {
  return {
    task_type: 'tlc', schema_version: 'tlc-v1', model_id: 'tlc-model-real', model_version: 'v2.1', artifact_sha256: 'a'.repeat(64),
    ensemble_weights: { rf: 1 }, mobile_phase_original: { hexane: 0.7, ethyl_acetate: 0.3 }, mobile_phase_normalized: { hexane: 0.7, ethyl_acetate: 0.3 },
    metrics: [], rows: [{ rf: 0 }], visualizations: [], applicability: { supported_scope: 'approved TLC scope', warnings: [{ code: 'SCOPE_NOTICE', message: '仅适用于已验证范围' }] }, ...overrides,
  }
}

function predictionDto(overrides: Record<string, unknown> = {}) {
  return {
    id: 'prediction-1', project_id: 'project-1', prediction_record_id: 'record-1', job_id: 'job-1', chromatography_type: 'tlc', tool: 'single', schema_version: 'tlc-v1', status: 'SUCCEEDED',
    model: { id: 'tlc-model-real', version: 'v2.1', business_scope_id: 'scope-1', artifact_sha256: 'a'.repeat(64), input_schema_version: 'tlc-v1', output_schema_version: 'tlc-v1', score_semantics: null, threshold: null },
    input_snapshot: {}, result: tlcResult(), warnings: [{ code: 'PREDICTION_NOTICE', message: '服务端真实警告' }], source: 'assistant', conversation_id: 'conversation-1', agent_run_id: 'run-1', created_at: '2026-10-06T00:00:00Z', completed_at: '2026-10-06T00:00:01Z',
    data_origin: 'production', revision: 1, etag: 'prediction-etag', updated_at: '2026-10-06T00:00:01Z', ...overrides,
  }
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

describe('AssistantView tool capability and result states', () => {
  it('closes conversation menus outside, keeps only one open, and returns focus after Escape', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAssistantStore()
    vi.spyOn(store, 'bootstrap').mockResolvedValue(undefined)
    store.conversations = [
      { id: 'menu-one', project_id: 'project-1', title: '会话一', status: 'ACTIVE' },
      { id: 'menu-two', project_id: 'project-1', title: '会话二', status: 'ACTIVE' },
    ] as never
    const wrapper = mount(AssistantView, { attachTo: document.body, global: { plugins: [pinia] } })
    try {
      const menus = wrapper.findAll('details.conversation-menu')
      const summaries = menus.map((menu) => menu.get('summary'))
      await summaries[0]!.trigger('click')
      await flushPromises()
      expect((menus[0]!.element as HTMLDetailsElement).open).toBe(true)

      await summaries[1]!.trigger('click')
      await flushPromises()
      expect((menus[0]!.element as HTMLDetailsElement).open).toBe(false)
      expect((menus[1]!.element as HTMLDetailsElement).open).toBe(true)

      const secondSummary = summaries[1]!.element as HTMLElement
      secondSummary.focus()
      await summaries[1]!.trigger('keydown', { key: 'Escape' })
      await flushPromises()
      expect((menus[1]!.element as HTMLDetailsElement).open).toBe(false)
      expect(document.activeElement).toBe(secondSummary)

      await summaries[0]!.trigger('click')
      await flushPromises()
      document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
      await flushPromises()
      expect((menus[0]!.element as HTMLDetailsElement).open).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })

  it('does not let outside pointer events affect the active archive confirmation', async () => {
    const { store, wrapper } = mountAssistantView()
    const confirmations = useConfirmationStore()
    store.projectId = 'project-1'
    store.conversations = [{ id: 'menu-archive', project_id: 'project-1', title: '待归档', status: 'ACTIVE' }] as never
    vi.spyOn(confirmations, 'loadPreview').mockResolvedValue({
      resource_ref: { type: 'conversation', id: 'menu-archive' }, action: 'resource.archive', can_confirm: true, summary: [], blockers: [], revision: 1,
    } as never)
    const archive = vi.spyOn(store, 'archiveConversation')
    await flushPromises()

    const menu = wrapper.get('details.conversation-menu')
    await menu.find('summary').trigger('click')
    await menu.findAll('button').find((button) => button.text() === '移出列表')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.archive-dialog').exists()).toBe(true)
    expect((menu.element as HTMLDetailsElement).open).toBe(false)

    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(wrapper.find('.archive-dialog').exists()).toBe(true)
    expect(archive).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('opens the mobile conversation drawer and returns focus after Escape or scrim dismissal', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAssistantStore()
    vi.spyOn(store, 'bootstrap').mockResolvedValue(undefined)
    store.conversations = [{ id: 'mobile-conversation', project_id: 'project-1', title: '手机会话', status: 'ACTIVE' }] as never
    const wrapper = mount(AssistantView, { attachTo: document.body, global: { plugins: [pinia] } })
    try {
      const trigger = wrapper.get('.mobile-menu-button')
      const triggerElement = trigger.element as HTMLButtonElement
      triggerElement.focus()
      await trigger.trigger('click')
      await flushPromises()

      expect(wrapper.classes()).toContain('mobile-sidebar-open')
      expect(wrapper.get('#assistant-conversation-list').text()).toContain('手机会话')

      await wrapper.get('#assistant-conversation-list').trigger('keydown', { key: 'Escape' })
      await flushPromises()
      expect(wrapper.classes()).not.toContain('mobile-sidebar-open')
      expect(document.activeElement).toBe(triggerElement)

      await trigger.trigger('click')
      await flushPromises()
      await wrapper.get('.sidebar-scrim').trigger('click')
      await flushPromises()
      expect(wrapper.classes()).not.toContain('mobile-sidebar-open')
      expect(document.activeElement).toBe(triggerElement)
    } finally {
      wrapper.unmount()
    }
  })

  it('blocks user dismissal while archiving but closes the dialog on success', async () => {
    const { store, wrapper } = mountAssistantView()
    const confirmations = useConfirmationStore()
    store.projectId = 'project-1'
    store.conversations = [{ id: 'conversation-archive', project_id: 'project-1', title: '归档测试', status: 'ACTIVE' }] as never
    store.selectedConversationId = 'conversation-archive'
    vi.spyOn(confirmations, 'loadPreview').mockImplementation(async () => {
      const preview = { resource_ref: { type: 'conversation', id: 'conversation-archive' }, action: 'resource.archive', can_confirm: true, summary: [], blockers: [], revision: 7 }
      confirmations.preview = preview as never
      return preview as never
    })
    vi.spyOn(confirmations, 'create').mockResolvedValue({ id: 'confirmation-archive' } as never)
    let finishArchive!: () => void
    const pending = new Promise<void>((resolve) => { finishArchive = resolve })
    const archive = vi.spyOn(store, 'archiveConversation').mockImplementation(async () => {
      await pending
      store.conversations = []
      store.selectedConversationId = null
      return { id: 'conversation-archive', status: 'ARCHIVED' } as never
    })
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '移出列表')!.trigger('click')
    await flushPromises()
    await wrapper.get('.archive-dialog .primary').trigger('click')
    await flushPromises()
    await wrapper.get('.archive-dialog').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('.archive-dialog').exists()).toBe(true)
    finishArchive()
    await flushPromises()
    expect(archive).toHaveBeenCalledOnce()
    expect(wrapper.find('.archive-dialog').exists()).toBe(false)
    expect(confirmations.preview).toBeNull()
    wrapper.unmount()
  })

  it('keeps the newest search cursor when an older query resolves late', async () => {
    vi.useFakeTimers()
    const { store, wrapper } = mountAssistantView()
    const calls: Array<{ q?: string; archived?: boolean; cursor?: string; append?: boolean }> = []
    let resolveFirst!: (value: never) => void
    let resolveSecond!: (value: never) => void
    const first = new Promise<never>((resolve) => { resolveFirst = resolve })
    const second = new Promise<never>((resolve) => { resolveSecond = resolve })
    vi.spyOn(store, 'listConversations').mockImplementation((options) => {
      const requestOptions = options ?? {}
      calls.push(requestOptions)
      if (requestOptions.q === 'first') return first
      if (requestOptions.q === 'second') return second
      return Promise.resolve({ page: { next_cursor: 'cursor-following', has_more: false } } as never)
    })

    const search = wrapper.get('input[aria-label="搜索会话"]')
    await search.setValue('first')
    await vi.advanceTimersByTimeAsync(250)
    await search.setValue('second')
    await vi.advanceTimersByTimeAsync(250)
    resolveSecond({ page: { next_cursor: 'cursor-second', has_more: true } } as never)
    await flushPromises()
    resolveFirst({ page: { next_cursor: 'cursor-first', has_more: true } } as never)
    await flushPromises()

    await wrapper.get('.load-more-button').trigger('click')
    await flushPromises()
    expect(calls[2]).toMatchObject({ q: 'second', archived: false, cursor: 'cursor-second', append: true })
    wrapper.unmount()
  })

  it('shows only first-round capabilities returned by the service', async () => {
    const { store, wrapper } = mountAssistantView()
    store.priorityToolsState = 'ready'
    store.priorityTools = [
      { description: 'single prediction', name: 'create_prediction', parameters: {}, permission: 'assistant:run', requires_confirmation: false, schema_version: '1.0' },
      { description: 'data version summary', name: 'get_data_version_summary', parameters: {}, permission: 'assistant:read', requires_confirmation: false, schema_version: '1.0' },
      { description: 'data summary', name: 'get_prediction_record_summary', parameters: {}, permission: 'assistant:read', requires_confirmation: false, schema_version: '1.0' },
      { description: 'model list', name: 'list_models', parameters: {}, permission: 'assistant:read', requires_confirmation: false, schema_version: '1.0' },
      { description: 'registration lookup', name: 'get_registration_draft', parameters: {}, permission: 'assistant:read', requires_confirmation: false, schema_version: '1.0' },
    ]
    await flushPromises()

    const capabilities = wrapper.get('.capability-panel').text()
    expect(capabilities).toContain('提交预测')
    expect(capabilities).toContain('数据版本摘要')
    expect(capabilities).toContain('预测记录摘要')
    expect(capabilities).toContain('模型列表')
    expect(capabilities).toContain('查询登记草稿')
    expect(capabilities).not.toContain('TLC 反推溶剂')
    wrapper.unmount()
  })

  it('displays the tool-directory error instead of claiming capabilities are available', async () => {
    const { store, wrapper } = mountAssistantView()
    store.priorityToolsState = 'error'
    store.priorityToolsError = 'NOT_FOUND: 工具目录暂不可用'
    await flushPromises()

    expect(wrapper.get('.capability-panel').text()).toContain('工具能力目录不可用')
    expect(wrapper.get('.capability-panel').text()).toContain('NOT_FOUND: 工具目录暂不可用')
    expect(wrapper.get('.capability-panel').text()).not.toContain('提交预测')
    wrapper.unmount()
  })

  it('does not display failed tool summary data as a successful result', async () => {
    const { store, wrapper } = mountAssistantView()
    store.priorityToolsState = 'ready'
    store.toolCalls = [{ id: 'tool-failed', tool_name: 'get_prediction_record_summary', status: 'FAILED', result_summary: { count: 99 } } as never]
    await flushPromises()

    expect(wrapper.text()).toContain('当前会话尚无摘要结果')
    expect(wrapper.text()).not.toContain('99')
    wrapper.unmount()
  })

  it('keeps a long resource reference visible in a tool result field', async () => {
    const { store, wrapper } = mountAssistantView()
    const resourceId = `resource_${'0123456789abcdef'.repeat(12)}`
    store.toolCalls = [{ id: 'tool-long-reference', tool_name: 'get_prediction_record_summary', status: 'SUCCEEDED', result_summary: { source_ref: resourceId } }] as never
    await flushPromises()

    const value = wrapper.get('.tool-result-card.data dd')
    expect(value.text()).toBe(resourceId)
    expect(wrapper.get('.insight-details').attributes('open')).toBeUndefined()
    expect(wrapper.get('.tool-result-card.data .wrap-result-field').classes()).toContain('wrap-result-field')
    wrapper.unmount()
  })

  it('marks a conversation only when its matching loaded run is active', async () => {
    const { store, wrapper } = mountAssistantView()
    store.conversations = [{ id: 'conversation-marker', project_id: 'project-1', title: '进行中', status: 'ACTIVE', current_run_id: 'run-marker' }] as never
    store.run = { id: 'run-marker', conversation_id: 'conversation-marker', status: 'RUNNING' } as never
    await flushPromises()

    const marker = wrapper.get('.conversation-item .run-mark')
    expect(marker.text()).toBe('●')
    expect(marker.attributes('title')).toBe('有进行中的业务回答')
    wrapper.unmount()
  })

  it.each([
    ['succeeded', { id: 'run-marker', conversation_id: 'conversation-marker', status: 'SUCCEEDED' }],
    ['failed', { id: 'run-marker', conversation_id: 'conversation-marker', status: 'FAILED' }],
    ['unknown status', { id: 'run-marker', conversation_id: 'conversation-marker', status: 'FUTURE_STATUS' }],
    ['not loaded', null],
    ['run ID mismatch', { id: 'other-run', conversation_id: 'conversation-marker', status: 'RUNNING' }],
    ['conversation mismatch', { id: 'run-marker', conversation_id: 'other-conversation', status: 'RUNNING' }],
  ])('does not mark a conversation for %s', async (_label, run) => {
    const { store, wrapper } = mountAssistantView()
    store.conversations = [{ id: 'conversation-marker', project_id: 'project-1', title: '已结束或未知', status: 'ACTIVE', current_run_id: 'run-marker' }] as never
    store.run = run as never
    await flushPromises()

    expect(wrapper.find('.conversation-item .run-mark').exists()).toBe(false)
    wrapper.unmount()
  })

  it('marks metadata-only tool calls as execution metadata instead of business summary', async () => {
    const { store, wrapper } = mountAssistantView()
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.priorityToolsState = 'ready'
    store.toolCalls = [{ id: 'tool-summary', tool_name: 'get_prediction_record_summary', status: 'SUCCEEDED', result_summary: { metadata_only: true, type: 'dict', count: 3, keys: ['numeric', 'status_counts'], result_hash: 'b'.repeat(64) } }] as never
    await flushPromises()

    const card = wrapper.get('.tool-result-card.data')
    expect(card.text()).toContain('工具执行元数据')
    expect(card.text()).toContain('不包含可展示的业务结果明细')
    expect(card.text()).not.toContain('3')
    expect(wrapper.get('.insight-panel').text()).toContain('当前会话尚无摘要结果')
    wrapper.unmount()
  })

  it('requeries a successful prediction resource and shows only real TLC result fields including Rf zero', async () => {
    const { store, wrapper } = mountAssistantView()
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.selectedConversationId = 'conversation-1'
    store.priorityToolsState = 'ready'
    store.toolCalls = [{ id: 'tool-create', tool_name: 'create_prediction', status: 'SUCCEEDED', resource_ref: { type: 'prediction', id: 'prediction-1' }, result_summary: { metadata_only: true } }] as never
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(predictionDto()))
    await flushPromises()

    const card = wrapper.get('.prediction-tool-result-card')
    await card.get('button').trigger('click')
    await flushPromises()

    expect(fetch).toHaveBeenCalledWith('/api/v1/predictions/prediction-1', expect.anything())
    expect(card.text()).toContain('预测 Rf：0')
    expect(card.text()).toContain('tlc-model-real')
    expect(card.text()).toContain('v2.1')
    expect(card.text()).toContain('SCOPE_NOTICE：仅适用于已验证范围')
    expect(card.text()).toContain('PREDICTION_NOTICE：服务端真实警告')
    expect(card.text()).toContain('a'.repeat(64))
    wrapper.unmount()
  })

  it.each([
    ['failed prediction', predictionDto({ status: 'FAILED', error: { code: 'MODEL_UNAVAILABLE', message: '模型服务暂不可用', retryable: true } })],
    ['successful response without a result', predictionDto({ result: null })],
    ['malformed result without applicability', predictionDto({ result: tlcResult({ applicability: undefined }) })],
    ['result without Rf rows', predictionDto({ result: tlcResult({ rows: [] }) })],
    ['result with a mismatched chromatography type', predictionDto({ result: tlcResult({ task_type: 'cc' }) })],
  ])('does not show TLC numeric results for %s', async (_label, response) => {
    const { store, wrapper } = mountAssistantView()
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.selectedConversationId = 'conversation-1'
    store.priorityToolsState = 'ready'
    store.toolCalls = [{ id: 'tool-create', tool_name: 'create_prediction', status: 'SUCCEEDED', resource_ref: { type: 'prediction', id: 'prediction-1' } }] as never
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(response))
    await flushPromises()

    const card = wrapper.get('.prediction-tool-result-card')
    await card.get('button').trigger('click')
    await flushPromises()

    expect(card.text()).not.toContain('预测 Rf')
    if (_label === 'failed prediction') expect(card.text()).toContain('MODEL_UNAVAILABLE：模型服务暂不可用')
    if (_label === 'successful response without a result') expect(card.text()).toContain('服务端未返回正式结果数据')
    if (_label === 'malformed result without applicability') expect(card.text()).toContain('RESULT_CONTRACT_INVALID')
    if (_label === 'result without Rf rows') expect(card.text()).toContain('未返回 Rf 行')
    if (_label === 'result with a mismatched chromatography type') expect(card.text()).toContain('结果类型 cc 与预测类型 tlc 不匹配')
    wrapper.unmount()
  })

  it('requeries only a successful registration resource reference and displays allowlisted fields', async () => {
    const { store, wrapper } = mountAssistantView()
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.priorityToolsState = 'ready'
    store.toolCalls = [
      { id: 'tool-create', tool_name: 'create_registration_draft', status: 'SUCCEEDED', resource_ref: { type: 'model_registration', id: 'registration-1' }, result_summary: { status: 'REGISTERED', source: 'untrusted summary' } },
      { id: 'tool-wrong-type', tool_name: 'get_registration_draft', status: 'SUCCEEDED', resource_ref: { type: 'prediction', id: 'registration-2' } },
      { id: 'tool-failed', tool_name: 'get_registration_draft', status: 'FAILED', resource_ref: { type: 'model_registration', id: 'registration-3' } },
    ] as never
    const draft = { id: 'registration-1', project_id: 'project-1', revision: 2, etag: 'etag-private', config_hash: 'a'.repeat(64), created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-02T00:00:00Z', name: '模型甲', status: 'DRAFT', task_type: 'tlc', source: { kind: 'external_package', artifact_id: 'artifact-private' }, manifest_snapshot: { internal: 'must-not-render' } }
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse(draft))
    await flushPromises()

    expect(wrapper.findAll('.registration-draft-card')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('untrusted summary')
    await wrapper.get('.registration-draft-card button').trigger('click')
    await flushPromises()

    expect(fetch).toHaveBeenCalledWith('/api/v1/assistant/priority/model-registration-drafts/registration-1?project_id=project-1', expect.anything())
    const card = wrapper.get('.registration-draft-card')
    expect(card.text()).toContain('模型甲')
    expect(card.text()).toContain('tlc')
    expect(card.text()).toContain('DRAFT')
    expect(card.text()).toContain('2')
    expect(card.text()).not.toContain('artifact-private')
    expect(card.text()).not.toContain('etag-private')
    expect(card.text()).not.toContain('must-not-render')
    wrapper.unmount()
  })

  it('does not render a prediction query card from failed, unrelated, wrong-type, or unsafe resource references', async () => {
    const { store, wrapper } = mountAssistantView()
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.priorityToolsState = 'ready'
    store.toolCalls = [
      { id: 'tool-failed', tool_name: 'create_prediction', status: 'FAILED', resource_ref: { type: 'prediction', id: 'prediction-failed' } },
      { id: 'tool-get', tool_name: 'get_prediction', status: 'SUCCEEDED', resource_ref: { type: 'prediction', id: 'prediction-get' } },
      { id: 'tool-wrong-type', tool_name: 'create_prediction', status: 'SUCCEEDED', resource_ref: { type: 'model_registration', id: 'prediction-wrong' } },
      { id: 'tool-unsafe', tool_name: 'create_prediction', status: 'SUCCEEDED', resource_ref: { type: 'prediction', id: '../private' } },
    ] as never
    await flushPromises()

    expect(wrapper.findAll('.prediction-tool-result-card')).toHaveLength(0)
    wrapper.unmount()
  })

  it('hides references until the project is resolved and replaces cached success with the real 404 error', async () => {
    const { store, wrapper } = mountAssistantView()
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'needs_selection' } as never
    store.priorityToolsState = 'ready'
    store.toolCalls = [{ id: 'tool-create', tool_name: 'create_registration_draft', status: 'SUCCEEDED', resource_ref: { type: 'model_registration', id: 'registration-1' } }] as never
    const draft = { id: 'registration-1', project_id: 'project-1', revision: 1, etag: 'etag', config_hash: 'b'.repeat(64), created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', name: '旧记录', status: 'DRAFT', task_type: 'sfc', source: { kind: 'external_package', artifact_id: 'artifact-1' } }
    let draftRequests = 0
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/v1/data-domains') return jsonResponse([])
      if (url === '/api/v1/prediction/types') return jsonResponse([])
      if (url.includes('/model-registration-drafts/')) {
        draftRequests += 1
        return draftRequests === 1
          ? jsonResponse(draft)
          : new Response(JSON.stringify({ error: { code: 'NOT_FOUND', message: '登记草稿不存在' } }), { status: 404, headers: { 'content-type': 'application/json' } })
      }
      throw new Error(`Unexpected request ${url}`)
    })
    await flushPromises()
    expect(wrapper.find('.registration-draft-card').exists()).toBe(false)

    store.accessContext = { project_selection: 'resolved' } as never
    await flushPromises()
    expect(wrapper.find('.registration-draft-card').exists()).toBe(true)
    await wrapper.get('.registration-draft-card button').trigger('click')
    await flushPromises()
    expect(wrapper.get('.registration-draft-card').text()).toContain('旧记录')

    await wrapper.get('.registration-draft-card button').trigger('click')
    await flushPromises()
    const card = wrapper.get('.registration-draft-card')
    expect(fetch).toHaveBeenCalledTimes(4)
    expect(draftRequests).toBe(2)
    expect(card.text()).toContain('NOT_FOUND: 登记草稿不存在')
    expect(card.text()).not.toContain('旧记录')
    expect(card.text()).not.toContain('DRAFT')
    wrapper.unmount()
  })

  it('queries explicit filters and renders only the validated summary DTO, safe source IDs, and real units', async () => {
    const { store, wrapper } = mountAssistantView()
    const requests: string[] = []
    const sourceIds = [...Array.from({ length: 12 }, (_, index) => `record-${index + 1}`), '/srv/private']
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      requests.push(url)
      if (url === '/api/v1/data-domains') return jsonResponse([{ id: 'domain-tlc', chromatography_type: 'tlc', version: 'v1', record_schema: {}, identity_fields: [], supported_import_modes: [], unit_definitions: {}, deduplication_policy: 'reject' }])
      if (url === '/api/v1/prediction/types') return jsonResponse([{ id: 'tlc', display_name: 'TLC', health: { status: 'READY' }, tools: ['single'] }])
      if (url.includes('/data-version-summary')) return jsonResponse({ project_id: 'project-1', domain_id: 'domain-tlc', count: 1, status_counts: { READY: 1 }, numeric: { flow_rate: { count: 1, minimum: 1, mean: 1, maximum: 1, unit: 'mL/min' } }, units: { flow_rate: 'mL/min' }, source: { source_type: 'data_version', source_ids: ['version-1'], version: 'v1' } })
      if (url.includes('/prediction-record-summary')) return jsonResponse({ project_id: 'project-1', chromatography_type: 'tlc', count: 1, status_counts: { SUCCEEDED: 1 }, missing: { batch_id: 1, archived_at: 1, 'model_snapshot.threshold': 1, 'model_snapshot.score_semantics': 1, api_key_length: 42 }, numeric: { revision: { count: 1, minimum: 2, mean: 2, maximum: 2, unit: null }, api_key_length: { count: 1, mean: 42 } }, source: { source_type: 'prediction_record', source_ids: sourceIds, version: null } })
      throw new Error(`Unexpected GET ${url}`)
    })
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.selectedConversationId = 'conversation-1'
    await flushPromises()

    const summaryCards = wrapper.findAll('.summary-query-card')
    await summaryCards[0]!.get('select').setValue('domain-tlc')
    await summaryCards[0]!.get('button').trigger('click')
    await flushPromises()
    expect(summaryCards[0]!.get('[data-testid="data-version-summary"]').text()).toContain('mL/min')
    expect(summaryCards[0]!.get('[data-testid="data-version-summary"]').text()).toContain('本次摘要条目数：1')

    await summaryCards[1]!.get('select').setValue('tlc')
    await summaryCards[1]!.get('button').trigger('click')
    await flushPromises()
    const recordSummary = summaryCards[1]!.get('[data-testid="prediction-record-summary"]').text()
    expect(requests).toContain('/api/v1/assistant/priority/data-version-summary?project_id=project-1&domain_id=domain-tlc')
    expect(requests).toContain('/api/v1/assistant/priority/prediction-record-summary?project_id=project-1&chromatography_type=tlc')
    expect(recordSummary).toContain('来源：prediction_record')
    expect(recordSummary).toContain('revision')
    expect(recordSummary).toContain('本次摘要条目数：1')
    expect(recordSummary).toContain('record-10')
    expect(recordSummary).toContain('batch_id 1')
    expect(recordSummary).toContain('model_snapshot.threshold 1')
    expect(recordSummary).toContain('另有 2 条未显示')
    expect(recordSummary).not.toContain('record-11')
    expect(recordSummary).not.toContain('/srv/private')
    expect(recordSummary).not.toContain('api_key_length')
    expect(recordSummary).not.toContain('Rf')
    expect(requests.every((url) => !/method=POST/i.test(url))).toBe(true)
    wrapper.unmount()
  })

  it('shows the true empty-domain state and never queries a data-version summary without a listed domain', async () => {
    const { store, wrapper } = mountAssistantView()
    const requests: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      requests.push(url)
      if (url === '/api/v1/data-domains') return jsonResponse([])
      if (url === '/api/v1/prediction/types') return jsonResponse([])
      throw new Error(`Unexpected request ${url}`)
    })
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    await flushPromises()

    expect(wrapper.get('.summary-query-section').text()).toContain('当前服务端未返回数据域')
    expect(wrapper.find('[data-testid="data-version-summary"]').exists()).toBe(false)
    expect(requests.some((url) => url.includes('/data-version-summary'))).toBe(false)
    wrapper.unmount()
  })

  it.each([
    ['data-domain', { project_id: 'project-1', domain_id: 'other-domain', count: 1, source: { source_type: 'data_version', source_ids: [], version: null } }],
    ['record project', { project_id: 'other-project', chromatography_type: 'tlc', count: 1, source: { source_type: 'prediction_record', source_ids: [], version: null } }],
    ['record source', { project_id: 'project-1', chromatography_type: 'tlc', count: 1, source: { source_type: 'data_version', source_ids: [], version: null } }],
    ['record type', { project_id: 'project-1', chromatography_type: 'hplc', count: 1, source: { source_type: 'prediction_record', source_ids: [], version: null } }],
  ])('rejects a summary with mismatched %s identity', async (kind, invalidSummary) => {
    const { store, wrapper } = mountAssistantView()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/v1/data-domains') return jsonResponse([{ id: 'domain-tlc', chromatography_type: 'tlc', version: 'v1', record_schema: {}, identity_fields: [], supported_import_modes: [], unit_definitions: {}, deduplication_policy: 'reject' }])
      if (url === '/api/v1/prediction/types') return jsonResponse([{ id: 'tlc', display_name: 'TLC', health: { status: 'READY' }, tools: ['single'] }])
      if (url.includes('/data-version-summary') || url.includes('/prediction-record-summary')) return jsonResponse(invalidSummary)
      throw new Error(`Unexpected request ${url}`)
    })
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    await flushPromises()
    const summaryCards = wrapper.findAll('.summary-query-card')
    const dataVersionCase = kind === 'data-domain'
    const card = dataVersionCase ? summaryCards[0]! : summaryCards[1]!
    if (dataVersionCase) await card.get('select').setValue('domain-tlc')
    else await card.get('select').setValue('tlc')
    await card.get('button').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="data-version-summary"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prediction-record-summary"]').exists()).toBe(false)
    expect(card.text()).toContain('服务端返回')
    wrapper.unmount()
  })

  it('keeps directory loading live when bootstrap and the same-context refresh overlap', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAssistantStore()
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'needs_selection' } as never
    vi.spyOn(store, 'bootstrap').mockImplementation(async () => { store.accessContext = { project_selection: 'resolved' } as never })
    const resolveRequests: Array<(response: Response) => void> = []
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise<Response>((resolve) => { resolveRequests.push(resolve) }))
    const wrapper = mount(AssistantView, { global: { plugins: [pinia] } })
    await flushPromises()

    expect(fetch).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.summary-query-section').text()).toContain('正在读取数据域目录')
    expect(wrapper.get('.summary-query-section').text()).toContain('正在读取色谱类型目录')
    resolveRequests[0]?.(jsonResponse([]))
    resolveRequests[1]?.(jsonResponse([]))
    await flushPromises()
    expect(wrapper.get('.summary-query-section').text()).toContain('当前服务端未返回数据域')
    wrapper.unmount()
  })

  it('shows a real summary API error and ignores a late response after the conversation changes', async () => {
    const { store, wrapper } = mountAssistantView()
    let completeLateResponse: ((response: Response) => void) | null = null
    let summaryRequests = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/v1/data-domains') return jsonResponse([])
      if (url === '/api/v1/prediction/types') return jsonResponse([{ id: 'tlc', display_name: 'TLC', health: { status: 'READY' }, tools: ['single'] }])
      if (url.includes('/prediction-record-summary')) {
        summaryRequests += 1
        if (summaryRequests === 1) return new Response(JSON.stringify({ error: { code: 'DEPENDENCY_UNAVAILABLE', message: '摘要服务暂不可用' } }), { status: 503, headers: { 'content-type': 'application/json' } })
        return new Promise<Response>((resolve) => { completeLateResponse = resolve })
      }
      throw new Error(`Unexpected request ${url}`)
    })
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.selectedConversationId = 'conversation-1'
    await flushPromises()
    const card = wrapper.findAll('.summary-query-card')[1]!
    await card.get('button').trigger('click')
    await flushPromises()
    expect(card.text()).toContain('DEPENDENCY_UNAVAILABLE: 摘要服务暂不可用')

    await card.get('button').trigger('click')
    await flushPromises()
    store.selectedConversationId = 'conversation-2'
    ;(completeLateResponse as unknown as (response: Response) => void)(jsonResponse({ project_id: 'project-1', count: 1, status_counts: { SUCCEEDED: 1 }, source: { source_type: 'prediction_record', source_ids: ['record-late'], version: null } }))
    await flushPromises()
    expect(wrapper.find('[data-testid="prediction-record-summary"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('record-late')
    wrapper.unmount()
  })

  it('clears a loaded summary and ignores its pending refresh when the project changes', async () => {
    const { store, wrapper } = mountAssistantView()
    let completeLateResponse: ((response: Response) => void) | null = null
    let summaryRequests = 0
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input)
      if (url === '/api/v1/data-domains') return jsonResponse([])
      if (url === '/api/v1/prediction/types') return jsonResponse([])
      if (url.includes('/prediction-record-summary')) {
        summaryRequests += 1
        if (summaryRequests === 1) return jsonResponse({ project_id: 'project-1', count: 1, status_counts: { SUCCEEDED: 1 }, source: { source_type: 'prediction_record', source_ids: ['record-project-one'], version: null } })
        return new Promise<Response>((resolve) => { completeLateResponse = resolve })
      }
      throw new Error(`Unexpected request ${url}`)
    })
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.selectedConversationId = 'conversation-1'
    await flushPromises()
    const card = wrapper.findAll('.summary-query-card')[1]!
    await card.get('button').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="prediction-record-summary"]').text()).toContain('record-project-one')

    await card.get('button').trigger('click')
    await flushPromises()
    store.projectId = 'project-2'
    await flushPromises()
    expect(wrapper.find('[data-testid="prediction-record-summary"]').exists()).toBe(false)
    ;(completeLateResponse as unknown as (response: Response) => void)(jsonResponse({ project_id: 'project-1', count: 1, status_counts: { SUCCEEDED: 1 }, source: { source_type: 'prediction_record', source_ids: ['record-late-project-one'], version: null } }))
    await flushPromises()
    expect(wrapper.find('[data-testid="prediction-record-summary"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('record-late-project-one')
    wrapper.unmount()
  })

  it('renders transient assistant text without fabricating a message or business result card', async () => {
    const { store, wrapper } = mountAssistantView()
    store.selectedConversationId = 'conversation-1'
    store.conversations = [{ id: 'conversation-1', project_id: 'project-1', title: '测试会话', status: 'ACTIVE' }] as never
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'RUNNING' } as never
    store.streamTextProjections = [{ conversation_id: 'conversation-1', run_id: 'run-1', message_id: 'message-pending', block_index: 0, text: '正在生成🙂', codePointLength: 5, revision: 1, incomplete: true }]
    await flushPromises()

    const projection = wrapper.get('.message-row.assistant')
    expect(wrapper.get('.progress-panel').attributes('open')).toBeUndefined()
    expect(wrapper.get('.progress-heading').text()).toContain('正在处理')
    expect(projection.text()).toContain('正在生成🙂')
    expect(projection.text()).toContain('回答尚未完整；等待服务端最终消息')
    expect(projection.get('.assistant-text').attributes('data-source-text')).toBe('正在生成🙂')
    expect(projection.get('.assistant-rendered').element.textContent?.trim()).toBe('正在生成🙂')
    expect(wrapper.find('.result-card').exists()).toBe(false)
    expect(store.messages).toEqual([])
    wrapper.unmount()
  })

  it.each([
    ['FAILED', false],
    ['INTERRUPTED', true],
  ])('offers recovery only for %s and waits for an explicit click', async (status, canRecover) => {
    const { store, wrapper } = mountAssistantView()
    store.run = { id: 'run-recovery', status, etag: 'run-etag' } as never
    const resumeRun = vi.spyOn(store, 'resumeRun').mockResolvedValue(undefined as never)
    await flushPromises()

    const recovery = wrapper.find('.recovery-actions button')
    expect(recovery.exists()).toBe(canRecover)
    expect(resumeRun).not.toHaveBeenCalled()
    if (canRecover) {
      expect(recovery.text()).toBe('恢复运行')
      await recovery.trigger('click')
      await flushPromises()
      expect(resumeRun).toHaveBeenCalledTimes(1)
      expect(resumeRun).toHaveBeenCalledWith('run-recovery', { reason: 'recovery' }, 'run-etag')
    }
    wrapper.unmount()
  })

  it('overlays a newer projection on its canonical text and blocks multiple pending confirmations', async () => {
    const { store, wrapper } = mountAssistantView()
    store.selectedConversationId = 'conversation-1'
    store.conversations = [{ id: 'conversation-1', project_id: 'project-1', title: '测试会话', status: 'ACTIVE' }] as never
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'WAITING_CONFIRMATION', pending_actions: [{ confirmation_id: 'confirm-1' }, { confirmation_id: 'confirm-2' }] } as never
    store.messages = [{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'PARTIAL', seq: '1', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '旧文本' }, { type: 'confirmation_request', action: 'create_prediction' }] }] as never
    store.streamTextProjections = [{ conversation_id: 'conversation-1', run_id: 'run-1', message_id: 'message-1', block_index: 0, text: '较新的文本', codePointLength: 5, revision: 2, incomplete: true }]
    await flushPromises()

    expect(wrapper.get('.message-row.assistant').text()).toContain('较新的文本')
    expect(wrapper.get('.message-row.assistant').text()).not.toContain('旧文本')
    expect(wrapper.text()).toContain('多个待确认动作')
    expect(wrapper.find('.confirmation-card button').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(['FAILED', 'PARTIAL'])('marks canonical %s text as ended when its Run failed', async (messageStatus) => {
    const { store, wrapper } = mountAssistantView()
    store.selectedConversationId = 'conversation-1'
    store.conversations = [{ id: 'conversation-1', project_id: 'project-1', title: '测试会话', status: 'ACTIVE' }] as never
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'FAILED' } as never
    store.messages = [{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: messageStatus, seq: '1', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '保留的部分文本' }] }] as never
    await flushPromises()

    expect(wrapper.get('.message-row.assistant').text()).toContain('运行已结束，保留未完成文本')
    expect(wrapper.get('.message-row.assistant').text()).not.toContain('等待服务端最终消息')
    wrapper.unmount()
  })

  it.each(['FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED', 'SUCCEEDED'])('marks a projection-only answer ended for terminal Run %s', async (runStatus) => {
    const { store, wrapper } = mountAssistantView()
    store.selectedConversationId = 'conversation-1'
    store.conversations = [{ id: 'conversation-1', project_id: 'project-1', title: '测试会话', status: 'ACTIVE' }] as never
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: runStatus } as never
    store.streamTextProjections = [{ conversation_id: 'conversation-1', run_id: 'run-1', message_id: 'message-pending', block_index: 0, text: '保留的部分文本', codePointLength: 7, revision: 1, incomplete: true }]
    await flushPromises()

    expect(wrapper.get('.message-row.assistant').text()).toContain('运行已结束，保留未完成文本')
    expect(wrapper.get('.message-row.assistant').text()).not.toContain('等待服务端最终消息')
    wrapper.unmount()
  })

  it('does not show an incomplete notice for a final canonical message', async () => {
    const { store, wrapper } = mountAssistantView()
    store.selectedConversationId = 'conversation-1'
    store.conversations = [{ id: 'conversation-1', project_id: 'project-1', title: '测试会话', status: 'ACTIVE' }] as never
    store.run = { id: 'run-1', conversation_id: 'conversation-1', status: 'SUCCEEDED' } as never
    store.messages = [{ id: 'message-1', conversation_id: 'conversation-1', role: 'assistant', status: 'FINAL', seq: '1', created_at: '2026-10-03T00:00:00Z', blocks: [{ type: 'text', text: '完整回答' }] }] as never
    await flushPromises()

    expect(wrapper.get('.message-row.assistant').text()).toContain('完整回答')
    expect(wrapper.get('.message-row.assistant').text()).not.toContain('回答尚未完整')
    expect(wrapper.get('.message-row.assistant').text()).not.toContain('运行已结束，保留未完成文本')
    wrapper.unmount()
  })

  it('shows the selected conversation mode and sends no client mode override', async () => {
    const { store, wrapper } = mountAssistantView()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]))
    const sendMessage = vi.spyOn(store, 'sendMessage').mockResolvedValue(undefined as never)
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.selectedConversationId = 'knowledge-conversation'
    store.conversations = [{ id: 'knowledge-conversation', project_id: 'project-1', mode: 'knowledge', title: '知识会话', status: 'ACTIVE' }] as never
    await flushPromises()

    expect(wrapper.get('.chat-heading').text()).toContain('知识模式')
    expect(wrapper.get('.composer-footer').text()).toContain('知识模式')
    await wrapper.get('textarea').setValue('请解释这个概念')
    await wrapper.get('.composer-footer .button.primary').trigger('click')
    await flushPromises()
    expect(sendMessage).toHaveBeenCalledWith('knowledge-conversation', { client_message_id: expect.any(String), content: '请解释这个概念' })
    wrapper.unmount()
  })

  it('shows the exact user text while sending and keeps IME Enter from submitting', async () => {
    const { store, wrapper } = mountAssistantView()
    store.projectId = 'project-1'
    store.accessContext = { project_selection: 'resolved' } as never
    store.selectedConversationId = 'conversation-raw'
    store.conversations = [{ id: 'conversation-raw', project_id: 'project-1', mode: 'business', title: '原文测试', status: 'ACTIVE' }] as never
    let finishSend!: () => void
    const send = vi.spyOn(store, 'sendMessage').mockReturnValue(new Promise((resolve) => { finishSend = () => resolve(undefined as never) }))
    await flushPromises()

    const input = wrapper.get('textarea')
    const raw = '  请保留首尾空格\n第二行  '
    await input.setValue(raw)
    await input.trigger('keydown', { key: 'Enter', keyCode: 229, isComposing: true })
    expect(send).not.toHaveBeenCalled()
    expect(input.element.value).toBe(raw)

    await input.trigger('keydown', { key: 'Enter', keyCode: 13, isComposing: false })
    await flushPromises()
    expect(send).toHaveBeenCalledWith('conversation-raw', { client_message_id: expect.any(String), content: raw })
    expect(wrapper.get('.pending-message .user-message-text').element.textContent).toBe(raw)
    finishSend()
    await flushPromises()
    wrapper.unmount()
  })

  it('keeps business mode labels and uses a neutral label for an unknown conversation mode', async () => {
    const { store, wrapper } = mountAssistantView()
    store.selectedConversationId = 'business-conversation'
    store.conversations = [{ id: 'business-conversation', project_id: 'project-1', mode: 'business', title: '业务会话', status: 'ACTIVE' }] as never
    await flushPromises()
    expect(wrapper.get('.chat-heading').text()).toContain('业务模式')
    expect(wrapper.get('.composer-footer').text()).toContain('业务模式')

    store.conversations = [{ id: 'business-conversation', project_id: 'project-1', mode: 'future-mode', title: '', status: 'ACTIVE' }] as never
    await flushPromises()
    expect(wrapper.get('.chat-heading').text()).toContain('未知模式')
    expect(wrapper.get('.composer-footer').text()).toContain('未知模式')
    expect(wrapper.get('.chat-heading').text()).not.toContain('业务模式')
    wrapper.unmount()
  })

  it('releases the delete busy state after success closes the modal', async () => {
    const { store, wrapper } = mountAssistantView()
    const confirmations = useConfirmationStore()
    store.projectId = 'project-1'
    store.selectedConversationId = 'delete-success'
    store.conversations = [{ id: 'delete-success', project_id: 'project-1', mode: 'business', title: '可删除', status: 'ACTIVE', etag: 'list-etag' }] as never
    const preview = { resource_ref: { type: 'conversation', id: 'delete-success' }, action: 'conversation.delete', can_confirm: true, summary: [], blockers: [], revision: 3, etag: 'preview-etag' }
    vi.spyOn(confirmations, 'loadPreview').mockImplementation(async () => { confirmations.preview = preview as never; return preview as never })
    vi.spyOn(confirmations, 'create').mockResolvedValue({ id: 'delete-confirmation' } as never)
    const remove = vi.spyOn(store, 'deleteConversation').mockResolvedValue({ id: 'delete-success', deleted: true } as never)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]))
    await flushPromises()

    await wrapper.findAll('button').find((button) => button.text().includes('删除聊天'))!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.dialog-actions button').find((button) => button.text().includes('确认删除聊天'))!.trigger('click')
    await flushPromises()

    expect(remove).toHaveBeenCalledWith('delete-success', { confirmation_id: 'delete-confirmation' }, expect.any(String), 'preview-etag', 'project-1')
    expect(wrapper.find('.delete-dialog').exists()).toBe(false)
    expect(wrapper.find('header .button.primary').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('invalidates only the old delete busy owner when switching projects during a request', async () => {
    const { store, wrapper } = mountAssistantView()
    const confirmations = useConfirmationStore()
    store.projectId = 'project-1'
    store.selectedConversationId = 'delete-old-project'
    store.conversations = [
      { id: 'delete-old-project', project_id: 'project-1', mode: 'business', title: '旧项目', status: 'ACTIVE', etag: 'old-list-etag' },
      { id: 'delete-new-project', project_id: 'project-1', mode: 'business', title: '新项目', status: 'ACTIVE', etag: 'new-list-etag' },
    ] as never
    vi.spyOn(confirmations, 'loadPreview').mockImplementation(async (resource) => {
      const preview = { resource_ref: { type: 'conversation', id: resource.id }, action: 'conversation.delete', can_confirm: true, summary: [], blockers: [], revision: 4, etag: `preview-${resource.id}` }
      confirmations.preview = preview as never
      return preview as never
    })
    let confirmationIndex = 0
    vi.spyOn(confirmations, 'create').mockImplementation(async () => ({ id: `delete-confirmation-${++confirmationIndex}` } as never))
    const finishDelete: Array<() => void> = []
    const remove = vi.spyOn(store, 'deleteConversation').mockImplementation((id) => new Promise((resolve) => { finishDelete.push(() => resolve({ id, deleted: true } as never)) }))
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]))
    await flushPromises()

    const findDeleteButton = (id: string) => wrapper.findAll('button').find((button) => button.text().includes('删除聊天') && button.element.closest('.conversation-entry')?.querySelector<HTMLElement>('.conversation-item')?.dataset.conversationId === id)!
    await findDeleteButton('delete-old-project').trigger('click')
    await flushPromises()
    await wrapper.findAll('.dialog-actions button').find((button) => button.text().includes('确认删除聊天'))!.trigger('click')
    await flushPromises()
    expect(finishDelete).toHaveLength(1)

    store.projectId = 'project-2'
    store.conversations = [{ id: 'delete-new-project', project_id: 'project-2', mode: 'business', title: '新项目', status: 'ACTIVE', etag: 'project-2-list-etag' }] as never
    await flushPromises()
    expect(wrapper.find('.delete-dialog').exists()).toBe(false)
    expect(wrapper.find('header .button.primary').attributes('disabled')).toBeUndefined()

    await findDeleteButton('delete-new-project').trigger('click')
    await flushPromises()
    await wrapper.findAll('.dialog-actions button').find((button) => button.text().includes('确认删除聊天'))!.trigger('click')
    await flushPromises()
    expect(finishDelete).toHaveLength(2)
    expect(wrapper.find('.dialog-actions button').attributes('disabled')).toBeDefined()
    await wrapper.get('.delete-dialog').trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('.delete-dialog').exists()).toBe(true)
    expect(remove).toHaveBeenCalledTimes(2)

    finishDelete[0]!()
    await flushPromises()
    expect(wrapper.find('.delete-dialog').exists()).toBe(true)
    expect(wrapper.find('.dialog-actions button').attributes('disabled')).toBeDefined()
    finishDelete[1]!()
    await flushPromises()
    expect(wrapper.find('.delete-dialog').exists()).toBe(false)
    expect(wrapper.find('header .button.primary').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('reuses the same confirmation and idempotency attempt after an uncertain transport failure', async () => {
    const { store, wrapper } = mountAssistantView()
    const confirmations = useConfirmationStore()
    store.projectId = 'project-1'
    store.conversations = [{ id: 'delete-retry', project_id: 'project-1', mode: 'business', title: '网络重试', status: 'ACTIVE', etag: 'list-etag' }] as never
    const preview = { resource_ref: { type: 'conversation', id: 'delete-retry' }, action: 'conversation.delete', can_confirm: true, summary: [], blockers: [], revision: 5, etag: 'preview-etag' }
    vi.spyOn(confirmations, 'loadPreview').mockImplementation(async () => { confirmations.preview = preview as never; return preview as never })
    const create = vi.spyOn(confirmations, 'create').mockResolvedValue({ id: 'retry-confirmation' } as never)
    const remove = vi.spyOn(store, 'deleteConversation')
      .mockRejectedValueOnce(new TypeError('connection reset after commit'))
      .mockResolvedValueOnce({ id: 'delete-retry', deleted: true } as never)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]))
    await flushPromises()

    await wrapper.findAll('button').find((button) => button.text().includes('删除聊天'))!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.dialog-actions button').find((button) => button.text().includes('确认删除聊天'))!.trigger('click')
    await flushPromises()
    expect(wrapper.get('.delete-dialog').text()).toContain('系统会复用本次确认与幂等键')

    await wrapper.findAll('.dialog-actions button').find((button) => button.text().includes('使用同一确认重试'))!.trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledOnce()
    expect(remove).toHaveBeenCalledTimes(2)
    expect(remove.mock.calls[1]).toEqual(remove.mock.calls[0])
    expect(wrapper.find('.delete-dialog').exists()).toBe(false)
    wrapper.unmount()
  })

  it('clears a formerly selected conversation draft after a lost-response retry without auto-creating a chat', async () => {
    const { store, wrapper } = mountAssistantView()
    const confirmations = useConfirmationStore()
    store.projectId = 'project-1'
    store.selectedConversationId = 'delete-selected-retry'
    store.conversations = [{ id: 'delete-selected-retry', project_id: 'project-1', mode: 'business', title: '当前聊天', status: 'ACTIVE', etag: 'list-etag' }] as never
    const preview = { resource_ref: { type: 'conversation', id: 'delete-selected-retry' }, action: 'conversation.delete', can_confirm: true, summary: [], blockers: [], revision: 6, etag: 'preview-etag' }
    vi.spyOn(confirmations, 'loadPreview').mockImplementation(async () => { confirmations.preview = preview as never; return preview as never })
    vi.spyOn(confirmations, 'create').mockResolvedValue({ id: 'retry-confirmation' } as never)
    const remove = vi.spyOn(store, 'deleteConversation')
      .mockRejectedValueOnce(new TypeError('connection reset after commit'))
      .mockResolvedValueOnce({ id: 'delete-selected-retry', deleted: true } as never)
    const create = vi.spyOn(store, 'createConversation').mockImplementation(async () => {
      store.selectedConversationId = 'manually-created'
      store.conversations = [{ id: 'manually-created', project_id: 'project-1', mode: 'business', title: '手动新建', status: 'ACTIVE' }] as never
      return { data: store.conversations[0] } as never
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]))
    await flushPromises()
    await wrapper.get('textarea').setValue('待删除会话里的草稿')
    await wrapper.findAll('button').find((button) => button.text().includes('删除聊天'))!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.dialog-actions button').find((button) => button.text().includes('确认删除聊天'))!.trigger('click')
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('待删除会话里的草稿')

    store.conversations = []
    store.selectedConversationId = null
    store.messages = []
    await wrapper.findAll('.dialog-actions button').find((button) => button.text().includes('使用同一确认重试'))!.trigger('click')
    await flushPromises()

    expect(remove).toHaveBeenCalledTimes(2)
    expect(remove.mock.calls[1]).toEqual(remove.mock.calls[0])
    expect(store.selectedConversationId).toBeNull()
    expect(wrapper.get('.welcome-state').text()).toContain('今天想处理什么')
    expect(create).not.toHaveBeenCalled()

    await wrapper.get('header .button.primary').trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledOnce()
    expect(wrapper.get('textarea').element.value).toBe('')
    wrapper.unmount()
  })

  it('keeps delete-dialog focus inside while loading, cycles focus, and restores the row menu on Escape', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAssistantStore()
    vi.spyOn(store, 'bootstrap').mockResolvedValue(undefined)
    const wrapper = mount(AssistantView, { attachTo: document.body, global: { plugins: [pinia] } })
    const confirmations = useConfirmationStore()
    store.projectId = 'project-1'
    store.selectedConversationId = 'focus-b'
    store.conversations = [
      { id: 'focus-b', project_id: 'project-1', mode: 'business', title: '当前会话 B', status: 'ACTIVE', etag: 'b-etag' },
      { id: 'focus-c', project_id: 'project-1', mode: 'business', title: '删除目标 C', status: 'ACTIVE', etag: 'c-etag' },
    ] as never
    let finishPreview!: () => void
    const preview = { resource_ref: { type: 'conversation', id: 'focus-c' }, action: 'conversation.delete', can_confirm: true, summary: [], blockers: [], revision: 7, etag: 'preview-c-etag' }
    vi.spyOn(confirmations, 'loadPreview').mockImplementation(() => new Promise((resolve) => { finishPreview = () => { confirmations.preview = preview as never; resolve(preview as never) } }))
    const remove = vi.spyOn(store, 'deleteConversation')
    await flushPromises()

    const menuItem = wrapper.findAll('.conversation-entry').find((entry) => entry.find('.conversation-item').attributes('data-conversation-id') === 'focus-c')!
    const trigger = menuItem.findAll('button').find((button) => button.text().includes('删除聊天'))!
    const menuSummary = menuItem.get('details.conversation-menu summary').element
    await trigger.trigger('click')
    await flushPromises()

    const dialog = wrapper.get('.delete-dialog')
    expect(document.activeElement).toBe(dialog.element)
    await dialog.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(dialog.element)

    finishPreview()
    await flushPromises()
    const first = dialog.get('.dialog-close').element as HTMLButtonElement
    const cancel = dialog.findAll('.dialog-actions button').find((button) => button.text() === '取消')!.element as HTMLButtonElement
    const last = dialog.findAll('button').filter((button) => !(button.element as HTMLButtonElement).disabled).at(-1)!.element as HTMLButtonElement
    expect(document.activeElement).toBe(cancel)

    first.focus()
    await dialog.trigger('keydown', { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
    last.focus()
    await dialog.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(first)

    await dialog.trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(wrapper.find('.delete-dialog').exists()).toBe(false)
    expect(document.activeElement).toBe(menuSummary)
    expect(remove).not.toHaveBeenCalled()

    vi.mocked(confirmations.loadPreview).mockRejectedValueOnce(new Error('preview unavailable'))
    await trigger.trigger('click')
    await flushPromises()
    const failedDialog = wrapper.get('.delete-dialog')
    expect(failedDialog.text()).toContain('重试读取确认')
    expect(document.activeElement).toBe(failedDialog.get('[data-delete-cancel]').element)
    await failedDialog.trigger('keydown', { key: 'Escape' })
    await flushPromises()
    expect(document.activeElement).toBe(menuSummary)
    expect(remove).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('refreshes the delete preview after confirmation creation reports CONFIRMATION_INVALID', async () => {
    const { store, wrapper } = mountAssistantView()
    const confirmations = useConfirmationStore()
    store.projectId = 'project-1'
    store.selectedConversationId = 'confirmation-current'
    store.conversations = [
      { id: 'confirmation-current', project_id: 'project-1', mode: 'business', title: '当前会话 B', status: 'ACTIVE', etag: 'b-etag' },
      { id: 'confirmation-target', project_id: 'project-1', mode: 'business', title: '目标会话 C', status: 'ACTIVE', etag: 'c-etag' },
    ] as never
    let previewRevision = 0
    const loadPreview = vi.spyOn(confirmations, 'loadPreview').mockImplementation(async () => {
      previewRevision += 1
      const preview = { resource_ref: { type: 'conversation', id: 'confirmation-target' }, action: 'conversation.delete', can_confirm: true, summary: [{ label: 'revision', value: previewRevision }], blockers: [], revision: previewRevision, etag: `preview-etag-${previewRevision}` }
      confirmations.preview = preview as never
      return preview as never
    })
    const create = vi.spyOn(confirmations, 'create')
      .mockRejectedValueOnce(new ApiClientError(409, 'CONFIRMATION_INVALID', '确认绑定与当前资源不一致'))
      .mockResolvedValueOnce({ id: 'confirmation-revision-2' } as never)
    const remove = vi.spyOn(store, 'deleteConversation').mockResolvedValue({ id: 'confirmation-target', deleted: true } as never)
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(jsonResponse([]))
    await flushPromises()

    const target = wrapper.findAll('.conversation-entry').find((entry) => entry.find('.conversation-item').attributes('data-conversation-id') === 'confirmation-target')!
    await target.findAll('button').find((button) => button.text().includes('删除聊天'))!.trigger('click')
    await flushPromises()
    await wrapper.findAll('.dialog-actions button').find((button) => button.text() === '确认删除聊天')!.trigger('click')
    await flushPromises()

    expect(create).toHaveBeenCalledOnce()
    expect(remove).not.toHaveBeenCalled()
    expect(loadPreview).toHaveBeenCalledTimes(2)
    expect(confirmations.preview).toMatchObject({ revision: 2, etag: 'preview-etag-2' })
    expect(wrapper.get('.delete-dialog').text()).toContain('已刷新确认预览，请检查后再次确认')
    expect(wrapper.findAll('.dialog-actions button').find((button) => button.text() === '确认删除聊天')!.attributes('disabled')).toBeUndefined()

    await wrapper.findAll('.dialog-actions button').find((button) => button.text() === '确认删除聊天')!.trigger('click')
    await flushPromises()
    expect(create).toHaveBeenCalledTimes(2)
    expect(remove).toHaveBeenCalledOnce()
    expect(remove).toHaveBeenCalledWith('confirmation-target', { confirmation_id: 'confirmation-revision-2' }, expect.any(String), 'preview-etag-2', 'project-1')
    expect(store.selectedConversationId).toBe('confirmation-current')
    wrapper.unmount()
  })
})
