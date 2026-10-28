import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StructureView from '@/views/StructureView.vue'
import TlcReverseWorkspace from '@/shared/tlc-reverse/TlcReverseWorkspace.vue'
import { useAppStore } from '@/stores/app'

function envelope(data: unknown) {
  return new Response(JSON.stringify({ data, request_id: 'access-context-loading-test', warnings: [] }), { status: 200, headers: { 'content-type': 'application/json' } })
}

const noProjects = { project_selection: 'no_accessible_projects', default_project_id: null, accessible_projects: [] }

beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })

describe('access-context loading in structure and TLC reverse entries', () => {
  it('shows a loading state in StructureView until the loaded context confirms no accessible projects', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const app = useAppStore()
    const wrapper = mount(StructureView, { global: { plugins: [pinia], stubs: { RouterLink: true, StructureInput: { template: '<div data-testid="structure-input" />' } } } })

    expect(wrapper.get('[data-testid="access-context-loading"]').attributes('role')).toBe('status')
    expect(wrapper.text()).toContain('正在读取服务端访问上下文')
    expect(wrapper.find('[data-testid="no-accessible-projects"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="structure-input"]').exists()).toBe(true)

    app.state = 'loading'
    await flushPromises()
    expect(wrapper.find('[data-testid="access-context-loading"]').exists()).toBe(true)

    app.accessContext = noProjects as never
    app.state = 'ready'
    await flushPromises()
    expect(wrapper.get('[data-testid="no-accessible-projects"]').text()).toContain('没有为当前访问上下文分配可访问项目')
    expect(wrapper.find('[data-testid="access-context-loading"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="structure-input"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps TLC reverse submission unavailable until access context resolves, then blocks an explicit empty context', async () => {
    let resolveAccessContext!: (response: Response) => void
    const accessContextResponse = new Promise<Response>((resolve) => { resolveAccessContext = resolve })
    const requests: Array<{ url: string; method: string }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input); const method = init?.method ?? 'GET'
      requests.push({ url, method })
      if (url.endsWith('/system/capabilities')) return envelope({ build_id: 'universal-v1', capabilities: [] })
      if (url.endsWith('/system/version')) return envelope({ application_version: '1.0', build_id: 'universal-v1', api_schema_version: '1.0', migration_version: '1', dependency_lock_hash: 'test' })
      if (url.endsWith('/system/access-context')) return accessContextResponse
      if (url.endsWith('/prediction/types/tlc')) return envelope({ id: 'tlc', display_name: 'TLC', tools: [], models: [], limits: {}, health: { status: 'UNAVAILABLE', reason: 'MODEL_UNAVAILABLE', checked_at: '' } })
      throw new Error(`Unexpected request ${url}`)
    }))
    const wrapper = mount(TlcReverseWorkspace, { global: { stubs: { RouterLink: true, StructureInput: true, TlcToolPanel: { template: '<button data-testid="reverse-submit">提交反推</button>' } } } })
    await flushPromises()

    expect(wrapper.text()).toContain('正在读取访问上下文')
    expect(wrapper.find('[data-testid="access-context-loading"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="no-accessible-projects"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="reverse-submit"]').exists()).toBe(false)
    expect((wrapper.get('[data-testid="tlc-reverse-project"]').element as HTMLSelectElement).disabled).toBe(true)

    resolveAccessContext(envelope(noProjects))
    await flushPromises()
    expect(wrapper.get('[data-testid="no-accessible-projects"]').text()).toContain('没有为当前访问上下文分配可访问项目')
    expect(wrapper.find('[data-testid="access-context-loading"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="reverse-submit"]').exists()).toBe(false)
    expect(requests.every((request) => request.method === 'GET')).toBe(true)
    wrapper.unmount()
  })

  it('does not mislabel a failed access-context request as an empty project assignment', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const app = useAppStore(); app.state = 'error'; app.error = 'SERVICE_UNAVAILABLE'
    const wrapper = mount(StructureView, { global: { plugins: [pinia], stubs: { RouterLink: true, StructureInput: { template: '<div data-testid="structure-input" />' } } } })

    expect(wrapper.get('[data-testid="access-context-error"]').text()).toContain('访问上下文暂不可用')
    expect(wrapper.find('[data-testid="no-accessible-projects"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="structure-input"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps the same local structure input through offline retry and restored access', async () => {
    const pinia = createPinia(); setActivePinia(pinia)
    const app = useAppStore(); app.state = 'error'
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    const wrapper = mount(StructureView, { global: { plugins: [pinia], stubs: { RouterLink: true, SharedKetcherCanvas: true } } })
    const input = wrapper.get('textarea')
    await input.setValue('C[C@H](O)C')
    app.state = 'loading'
    await flushPromises()
    expect(wrapper.get('textarea').element).toBe(input.element)
    expect((input.element as HTMLTextAreaElement).disabled).toBe(false)
    app.accessContext = { project_selection: 'resolved', default_project_id: 'offline-test-project', accessible_projects: [{ id: 'offline-test-project' }] } as never
    app.state = 'ready'
    await flushPromises()
    expect(wrapper.get('textarea').element).toBe(input.element)
    expect((input.element as HTMLTextAreaElement).value).toBe('C[C@H](O)C')
    expect(fetchMock).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
