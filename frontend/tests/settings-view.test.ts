import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SettingsView from '@/views/SettingsView.vue'
import type { components } from '@/modules/settings/contracts/revision14.generated'

type Configuration = components['schemas']['LlmConfigurationView']
type Settings = components['schemas']['LlmSettingsView']
type TestResult = components['schemas']['LlmTestResult']

function configuration(overrides: Partial<Configuration> = {}): Configuration {
  return {
    id: 'candidate-1', name: '候选模型', revision: '2', etag: '"candidate-2"', api_format: 'openai_chat',
    base_url: 'https://provider.example/v1', model_id: 'model-a', has_api_key: false, status: 'CANDIDATE',
    limits: { timeout_seconds: 90, max_retries: 2, max_output_tokens: 4096 },
    external_send_policy: 'deny_external', created_at: '2026-10-06T00:00:00Z', ...overrides,
  }
}

function settings(overrides: Partial<Settings> = {}): Settings {
  return {
    active_configuration: configuration({ id: 'active-1', name: '当前活动', revision: '1', status: 'ACTIVE', has_api_key: true }),
    active_revision: 1,
    candidate_configurations: [
      configuration(),
      configuration({ id: 'retired-1', name: '已退役模型', revision: '1', status: 'RETIRED' }),
    ],
    ...overrides,
  }
}

function testResult(overrides: Partial<TestResult> = {}): TestResult {
  return {
    id: 'test-1', configuration_id: 'candidate-1', configuration_revision: '2', status: 'SUCCEEDED',
    checks: { connectivity: 'PASSED', streaming: 'PASSED', tool_calling: 'PASSED', structured_output: 'NOT_SUPPORTED' },
    created_at: '2026-10-06T00:00:00Z', ...overrides,
  }
}

function response(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ data, request_id: 'settings-view-test', warnings: [] }), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  })
}

function publicSettingsResponse() {
  return response({
    product_name: 'Chromatography Platform', build_id: 'build-9', built_modules: ['universal-v1'], allowed_modes: ['business'],
    input_limits: { max_items: 10 }, export_limits: { max_bytes: 2048 },
  })
}

function errorResponse(status: number, code: string, message = 'service detail') {
  return new Response(JSON.stringify({ error: { code, message, field_errors: [] }, request_id: 'settings-view-test' }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function mountSettings() {
  const pinia = createPinia()
  const wrapper = mount(SettingsView, { global: { plugins: [pinia] } })
  return { pinia, wrapper }
}

function successfulSettingsFetch(data = settings(), etag = '"settings-1"') {
  return vi.spyOn(globalThis, 'fetch')
    .mockResolvedValueOnce(publicSettingsResponse())
    .mockResolvedValueOnce(response(data, 200, { ETag: etag }))
}

function candidateCard(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll('.configuration-item').find((item) => item.text().includes('候选模型'))!
}

async function runCandidateTest(wrapper: ReturnType<typeof mount>, name = '候选模型') {
  const card = wrapper.findAll('.configuration-item').find((item) => item.text().includes(name))!
  await card.findAll('button').find((button) => button.text().includes('测试此配置'))?.trigger('click')
}

async function selectCandidate(wrapper: ReturnType<typeof mount>, name: string) {
  const card = wrapper.findAll('.configuration-item').find((item) => item.text().includes(name))!
  await card.findAll('button').find((button) => button.text() === '选择')?.trigger('click')
}

afterEach(() => {
  vi.restoreAllMocks()
  window.localStorage?.clear()
})

describe('SettingsView LLM configuration flow', () => {
  it('keeps settings sections constrained to the mobile content width', () => {
    const view = readFileSync(resolve(process.cwd(), 'src/views/SettingsView.vue'), 'utf8')

    expect(view).toMatch(/\.settings-view\s*\{[^}]*min-width:\s*0/)
    expect(view).toMatch(/\.settings-view\s*>\s*:not\(\.page-header\)\s*\{[^}]*width:\s*100%[^}]*min-width:\s*0/)
    expect(view).toMatch(/\.settings-runtime \.facts\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/)
    expect(view).toMatch(/@media\s*\(max-width:\s*900px\)\s*\{[^}]*\.settings-runtime \.facts\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/)
  })

  it('renders the service-returned active, candidate and retired configurations after loading', async () => {
    const fetch = successfulSettingsFetch()
    const { wrapper } = mountSettings()
    expect(wrapper.text()).toContain('正在读取服务端配置')
    await flushPromises()

    expect(String(fetch.mock.calls[0][0])).toBe('/api/v1/settings/public')
    expect(String(fetch.mock.calls[1][0])).toBe('/api/v1/settings/llm')
    expect(wrapper.text()).toContain('当前活动')
    expect(wrapper.text()).toContain('候选模型')
    expect(wrapper.text()).toContain('已退役模型')
    expect(String(fetch.mock.calls[1][0])).toBe('/api/v1/settings/llm')
    expect(String(fetch.mock.calls[0][0])).toBe('/api/v1/settings/public')
    expect(wrapper.text()).toContain('Chromatography Platform')
    expect(wrapper.text()).toContain('build-9')
    expect(wrapper.text()).toContain('universal-v1')
    expect((wrapper.get('input[name="base-url"]').element as HTMLInputElement).pattern).toBe('https://.+')
    expect((wrapper.get('input[name="max-retries"]').element as HTMLInputElement).max).toBe('5')
    expect((wrapper.get('input[name="max-output-tokens"]').element as HTMLInputElement).max).toBe('32768')
    expect(wrapper.text()).toContain('完整 HTTPS base endpoint（scheme/host/effective port/base path）')
    await selectCandidate(wrapper, '候选模型')
    expect(wrapper.text()).toContain('尚未测试')
    expect(fetch).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('restores a candidate last_test_id and displays its real FAILED checks without starting a new test', async () => {
    const candidate = configuration({ last_test_id: 'test-failed' })
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings({ candidate_configurations: [candidate] }), 200, { ETag: '"settings-1"' }))
      .mockResolvedValueOnce(response(testResult({
        id: 'test-failed', status: 'FAILED',
        checks: { connectivity: 'FAILED', streaming: 'FAILED', tool_calling: 'FAILED', structured_output: 'NOT_SUPPORTED' },
      })))
    const { wrapper } = mountSettings()
    await flushPromises()
    await selectCandidate(wrapper, '候选模型')
    await flushPromises()

    expect(String(fetch.mock.calls[2][0])).toBe('/api/v1/settings/llm/tests/test-failed')
    expect(String(fetch.mock.calls[2][1]?.method ?? 'GET')).toBe('GET')
    expect(fetch).toHaveBeenCalledTimes(3)
    expect(wrapper.text()).toContain('测试未通过')
    expect(wrapper.text()).toContain('连通性')
    expect(wrapper.text()).toContain('流式响应')
    expect(wrapper.text()).not.toContain('尚未测试')
    expect(wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))?.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('shows a safe query failure for a missing persisted test resource instead of “untested”', async () => {
    const candidate = configuration({ last_test_id: 'test-missing' })
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings({ candidate_configurations: [candidate] }), 200, { ETag: '"settings-1"' }))
      .mockResolvedValueOnce(errorResponse(404, 'TEST_RESOURCE_NOT_FOUND', 'private provider detail'))
    const { wrapper } = mountSettings()
    await flushPromises()
    await selectCandidate(wrapper, '候选模型')
    await flushPromises()

    expect(String(fetch.mock.calls[2][0])).toBe('/api/v1/settings/llm/tests/test-missing')
    expect(wrapper.text()).toContain('测试状态读取失败')
    expect(wrapper.text()).toContain('TEST_RESOURCE_NOT_FOUND')
    expect(wrapper.text()).not.toContain('尚未测试')
    expect(wrapper.text()).not.toContain('private provider detail')
    expect(fetch).toHaveBeenCalledTimes(3)
    wrapper.unmount()
  })

  it('ignores a late previous candidate result after switching candidates', async () => {
    const first = configuration({ last_test_id: 'test-first' })
    const second = configuration({ id: 'candidate-2', name: '第二候选', revision: '6', last_test_id: 'test-second' })
    let resolveLateFirst: (value: Response) => void = () => {}
    const lateResponse = new Promise<Response>((resolveResponse) => { resolveLateFirst = resolveResponse })
    let firstSignal: AbortSignal | null | undefined
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/v1/settings/public') return publicSettingsResponse()
      if (url === '/api/v1/settings/llm') return response(settings({ candidate_configurations: [first, second] }), 200, { ETag: '"settings-1"' })
      if (url.endsWith('/tests/test-first')) { firstSignal = init?.signal; return lateResponse }
      if (url.endsWith('/tests/test-second')) return response(testResult({
        id: 'test-second', configuration_id: 'candidate-2', configuration_revision: '6', status: 'FAILED',
        checks: { connectivity: 'FAILED', streaming: 'FAILED', tool_calling: 'FAILED', structured_output: 'NOT_SUPPORTED' },
      }))
      return errorResponse(500, 'UNEXPECTED_TEST_REQUEST')
    })
    const { wrapper } = mountSettings()
    await flushPromises()
    await selectCandidate(wrapper, '候选模型')
    await flushPromises()
    expect(firstSignal?.aborted).toBe(false)

    await selectCandidate(wrapper, '第二候选')
    await flushPromises()
    expect(firstSignal?.aborted).toBe(true)
    expect(String(fetch.mock.calls[3][0])).toBe('/api/v1/settings/llm/tests/test-second')
    expect(wrapper.text()).toContain('第二候选')
    expect(wrapper.text()).toContain('测试未通过')

    resolveLateFirst(response(testResult({
      id: 'test-first', configuration_id: 'candidate-1', configuration_revision: '2', status: 'SUCCEEDED',
    })))
    await flushPromises()
    expect(wrapper.text()).toContain('测试未通过')
    expect(wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))?.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('refresh cancels old candidate polling and restores the latest last_test_id only', async () => {
    const oldCandidate = configuration({ last_test_id: 'test-old' })
    const newCandidate = configuration({ last_test_id: 'test-new' })
    let resolveLateOld: (value: Response) => void = () => {}
    const lateResponse = new Promise<Response>((resolveResponse) => { resolveLateOld = resolveResponse })
    let oldSignal: AbortSignal | null | undefined
    let settingsRequests = 0
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      if (url === '/api/v1/settings/public') return publicSettingsResponse()
      if (url === '/api/v1/settings/llm') {
        settingsRequests += 1
        return response(settings({ candidate_configurations: [settingsRequests === 1 ? oldCandidate : newCandidate] }), 200, { ETag: `"settings-${settingsRequests}"` })
      }
      if (url.endsWith('/tests/test-old')) { oldSignal = init?.signal; return lateResponse }
      if (url.endsWith('/tests/test-new')) return response(testResult({
        id: 'test-new', status: 'FAILED',
        checks: { connectivity: 'FAILED', streaming: 'FAILED', tool_calling: 'FAILED', structured_output: 'NOT_SUPPORTED' },
      }))
      return errorResponse(500, 'UNEXPECTED_TEST_REQUEST')
    })
    const { wrapper } = mountSettings()
    await flushPromises()
    await selectCandidate(wrapper, '候选模型')
    await flushPromises()
    expect(oldSignal?.aborted).toBe(false)

    await wrapper.findAll('button').find((button) => button.text() === '刷新配置')?.trigger('click')
    await flushPromises()
    expect(oldSignal?.aborted).toBe(true)
    expect(String(fetch.mock.calls[4][0])).toBe('/api/v1/settings/llm/tests/test-new')
    expect(wrapper.text()).toContain('测试未通过')
    expect(wrapper.text()).not.toContain('测试完成')

    resolveLateOld(response(testResult({ id: 'test-old', status: 'SUCCEEDED' })))
    await flushPromises()
    expect(wrapper.text()).toContain('测试未通过')
    expect(wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))?.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('shows a safe failure state when public runtime settings cannot be loaded', async () => {
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(errorResponse(503, 'PUBLIC_SETTINGS_UNAVAILABLE', 'private runtime detail'))
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
    const { wrapper } = mountSettings()
    await flushPromises()

    expect(wrapper.text()).toContain('公开运行设置读取失败')
    expect(wrapper.text()).toContain('PUBLIC_SETTINGS_UNAVAILABLE')
    expect(wrapper.text()).not.toContain('private runtime detail')
    expect(wrapper.text()).toContain('候选模型')
    wrapper.unmount()
  })

  it('reports GET failure safely and disables activation while retained settings are stale', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
      .mockResolvedValueOnce(errorResponse(503, 'SETTINGS_UNAVAILABLE', 'sensitive service detail'))
    const { wrapper } = mountSettings()
    await flushPromises()
    await wrapper.findAll('button').find((button) => button.text() === '刷新配置')?.trigger('click')
    await flushPromises()

    expect(fetch).toHaveBeenCalledTimes(3)
    expect(wrapper.text()).toContain('SETTINGS_UNAVAILABLE')
    expect(wrapper.text()).toContain('当前展示的是上次读取结果，激活已禁用')
    expect(wrapper.text()).not.toContain('sensitive service detail')
    await candidateCard(wrapper).findAll('button').find((button) => button.text() === '选择')?.trigger('click')
    expect(wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))?.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('submits controlled fields and a one-time key, then clears it without persisting it', async () => {
    const marker = 'view-test-secret-marker'
    const candidate = configuration()
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
      .mockResolvedValueOnce(response(candidate, 201, { ETag: '"candidate-2"', Location: '/api/v1/settings/llm/configurations/candidate-1' }))
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
    const { pinia, wrapper } = mountSettings()
    await flushPromises()
    await wrapper.get('input[name="configuration-name"]').setValue('内部助手')
    await wrapper.get('input[name="base-url"]').setValue('https://approved.example/api/v1')
    await wrapper.get('input[name="model-id"]').setValue('assistant-model')
    await wrapper.get('input[name="max-retries"]').setValue('5')
    await wrapper.get('input[name="max-output-tokens"]').setValue('32768')
    await wrapper.get('input[name="api-key-once"]').setValue(marker)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const body = JSON.parse(String(fetch.mock.calls[2][1]?.body))
    expect(body).toEqual({
      name: '内部助手', api_format: 'openai_chat', base_url: 'https://approved.example/api/v1', model_id: 'assistant-model',
      timeout_seconds: 90, max_retries: 5, max_output_tokens: 32768, external_send_policy: 'deny_external', api_key: marker,
    })
    expect((wrapper.get('input[name="api-key-once"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).not.toContain(marker)
    expect(JSON.stringify(pinia.state.value)).not.toContain(marker)
    expect(window.localStorage?.length ?? 0).toBe(0)
    const viewSource = readFileSync(resolve(process.cwd(), 'src/views/SettingsView.vue'), 'utf8')
    expect(viewSource).not.toMatch(/\b(?:localStorage|sessionStorage)\b/)
    expect(wrapper.text()).toContain('候选配置已由服务端创建')
    wrapper.unmount()
  })

  it('clears the key after a failed create and never renders the service message or key', async () => {
    const marker = 'failed-create-secret-marker'
    vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
      .mockResolvedValueOnce(errorResponse(422, 'INVALID_CONFIGURATION', marker))
    const { pinia, wrapper } = mountSettings()
    await flushPromises()
    await wrapper.get('input[name="configuration-name"]').setValue('配置')
    await wrapper.get('input[name="base-url"]').setValue('https://provider.example/v1')
    await wrapper.get('input[name="model-id"]').setValue('model')
    await wrapper.get('input[name="api-key-once"]').setValue(marker)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect((wrapper.get('input[name="api-key-once"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('INVALID_CONFIGURATION')
    expect(wrapper.text()).not.toContain(marker)
    expect(JSON.stringify(pinia.state.value)).not.toContain(marker)
    expect(window.localStorage?.length ?? 0).toBe(0)
    wrapper.unmount()
  })

  it('allows reuse only from server-listed configurations with a key and sends only the reference', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
      .mockResolvedValueOnce(response(configuration({ id: 'candidate-reuse', name: '复用配置' }), 201, { ETag: '"candidate-reuse"', Location: '/api/v1/settings/llm/configurations/candidate-reuse' }))
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
    const { wrapper } = mountSettings()
    await flushPromises()
    await wrapper.get('input[name="secret-entry-mode"][value="reuse"]').setValue()
    await wrapper.get('input[name="configuration-name"]').setValue('复用配置')
    await wrapper.get('input[name="base-url"]').setValue('https://provider.example/v1')
    await wrapper.get('input[name="model-id"]').setValue('model')
    await wrapper.get('select[name="reuse-secret"]').setValue('active-1')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const body = JSON.parse(String(fetch.mock.calls[2][1]?.body))
    expect(body.reuse_secret_from_configuration_id).toBe('active-1')
    expect(body).not.toHaveProperty('api_key')
    expect(wrapper.text()).toContain('当前活动')
    wrapper.unmount()
  })

  it('requires a successful same-candidate test with streaming and tool calling before activation', async () => {
    const passing = settings()
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(passing, 200, { ETag: '"settings-7"' }))
      .mockResolvedValueOnce(response({ id: 'test-1', job_id: 'job-1', resource_ref: { type: 'llm_test', id: 'test-1' }, status: 'QUEUED' }, 202, { Location: '/api/v1/settings/llm/tests/test-1' }))
      .mockResolvedValueOnce(response(testResult({ checks: { connectivity: 'PASSED', streaming: 'FAILED', tool_calling: 'PASSED', structured_output: 'NOT_SUPPORTED' } })))
      .mockResolvedValueOnce(response({ id: 'test-2', job_id: 'job-2', resource_ref: { type: 'llm_test', id: 'test-2' }, status: 'QUEUED' }, 202, { Location: '/api/v1/settings/llm/tests/test-2' }))
      .mockResolvedValueOnce(response(testResult({ id: 'test-2' })))
      .mockResolvedValueOnce(response(settings({ active_configuration: configuration({ id: 'candidate-1', name: '候选模型', revision: '2', status: 'ACTIVE' }), active_revision: 2, candidate_configurations: [] }), 200, { ETag: '"settings-8"' }))
    const { wrapper } = mountSettings()
    await flushPromises()
    await runCandidateTest(wrapper)
    await flushPromises()
    const activation = wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))
    expect(activation?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('不支持')

    await wrapper.findAll('button').find((button) => button.text().includes('重新测试'))?.trigger('click')
    await flushPromises()
    expect(activation?.attributes('disabled')).toBeUndefined()
    const currentActivation = wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))
    expect(currentActivation?.attributes('disabled')).toBeUndefined()
    await currentActivation?.trigger('click')
    await flushPromises()

    const [url, init] = fetch.mock.calls[6]
    expect(String(url)).toBe('/api/v1/settings/llm/configurations/candidate-1/activate')
    expect(new Headers(init?.headers).get('If-Match')).toBe('"settings-7"')
    expect(new Headers(init?.headers).get('Idempotency-Key')).toMatch(/^idem-/)
    expect(JSON.parse(String(init?.body))).toEqual({ test_id: 'test-2', expected_active_revision: 1 })
    expect(wrapper.text()).toContain('候选配置已激活')
    wrapper.unmount()
  })

  it('renders authentication-skipped checks safely and never enables activation for a failed test', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-7"' }))
      .mockResolvedValueOnce(response({ id: 'test-auth-failed', job_id: 'job-auth-failed', resource_ref: { type: 'llm_test', id: 'test-auth-failed' }, status: 'QUEUED' }, 202, { Location: '/api/v1/settings/llm/tests/test-auth-failed' }))
      .mockResolvedValueOnce(response({
        id: 'test-auth-failed', configuration_id: 'candidate-1', configuration_revision: '2', status: 'FAILED',
        checks: { connectivity: 'FAILED', streaming: 'SKIPPED', tool_calling: 'SKIPPED', structured_output: 'SKIPPED' },
        error: { code: 'PROVIDER_AUTH_FAILED', message: 'private provider detail' },
      }))
    const { wrapper } = mountSettings()
    await flushPromises()

    await runCandidateTest(wrapper)
    await flushPromises()

    expect(wrapper.text()).toContain('测试未通过')
    expect(wrapper.text()).toContain('连通性')
    expect(wrapper.text()).toContain('未执行（认证失败）')
    expect(wrapper.text()).not.toContain('private provider detail')
    expect(wrapper.text()).toContain('当前活动')
    const activation = wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))
    expect(activation?.attributes('disabled')).toBeDefined()
    expect(fetch.mock.calls.some(([url]) => String(url).includes('/activate'))).toBe(false)
    wrapper.unmount()
  })

  it('requires connectivity to pass and treats a skipped required check as not activatable', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-7"' }))
      .mockResolvedValueOnce(response({ id: 'test-connectivity-skipped', job_id: 'job-connectivity-skipped', resource_ref: { type: 'llm_test', id: 'test-connectivity-skipped' }, status: 'QUEUED' }, 202, { Location: '/api/v1/settings/llm/tests/test-connectivity-skipped' }))
      .mockResolvedValueOnce(response({
        id: 'test-connectivity-skipped', configuration_id: 'candidate-1', configuration_revision: '2', status: 'SUCCEEDED',
        checks: { connectivity: 'SKIPPED', streaming: 'PASSED', tool_calling: 'PASSED', structured_output: 'NOT_SUPPORTED' },
      }))
    const { wrapper } = mountSettings()
    await flushPromises()

    await runCandidateTest(wrapper)
    await flushPromises()

    expect(wrapper.text()).toContain('测试完成')
    expect(wrapper.text()).toContain('未执行（认证失败）')
    expect(wrapper.text()).toContain('不支持')
    const activation = wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))
    expect(activation?.attributes('disabled')).toBeDefined()
    expect(fetch.mock.calls.some(([url]) => String(url).includes('/activate'))).toBe(false)
    wrapper.unmount()
  })

  it('creates a candidate, polls the accepted test to terminal success, then activates with fresh revision and ETag', async () => {
    const candidate = configuration({ id: 'created-candidate', name: '刚创建候选', revision: '3' })
    const active = configuration({ id: 'active-1', name: '当前活动', revision: '4', status: 'ACTIVE', has_api_key: true })
    const afterCreate: Settings = { active_configuration: active, active_revision: 4, candidate_configurations: [candidate] }
    const afterActivate: Settings = {
      active_configuration: { ...candidate, status: 'ACTIVE' }, active_revision: 5, candidate_configurations: [],
    }
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response({ active_configuration: active, active_revision: 4, candidate_configurations: [] }, 200, { ETag: '"settings-4"' }))
      .mockResolvedValueOnce(response(candidate, 201, { ETag: '"created-candidate-3"', Location: '/api/v1/settings/llm/configurations/created-candidate' }))
      .mockResolvedValueOnce(response(afterCreate, 200, { ETag: '"settings-4b"' }))
      .mockResolvedValueOnce(response({ id: 'test-created', job_id: 'job-created', resource_ref: { type: 'llm_test', id: 'test-created' }, status: 'QUEUED' }, 202, { Location: '/api/v1/settings/llm/tests/test-created' }))
      .mockResolvedValueOnce(response(testResult({ id: 'test-created', configuration_id: 'created-candidate', configuration_revision: '3' })))
      .mockResolvedValueOnce(response(afterActivate, 200, { ETag: '"settings-5"' }))
    const { wrapper } = mountSettings()
    await flushPromises()
    await wrapper.get('input[name="configuration-name"]').setValue('刚创建候选')
    await wrapper.get('input[name="base-url"]').setValue('https://provider.example/v1')
    await wrapper.get('input[name="model-id"]').setValue('model-a')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await runCandidateTest(wrapper, '刚创建候选')
    await flushPromises()
    expect(wrapper.text()).toContain('测试完成')
    const activation = wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))
    expect(activation?.attributes('disabled')).toBeUndefined()
    await activation?.trigger('click')
    await flushPromises()

    const [testUrl, testInit] = fetch.mock.calls[4]
    expect(String(testUrl)).toBe('/api/v1/settings/llm/configurations/created-candidate/test')
    expect(new Headers(testInit?.headers).get('Idempotency-Key')).toMatch(/^idem-/)
    expect(JSON.parse(String(testInit?.body))).toEqual({})
    expect(String(fetch.mock.calls[5][0])).toBe('/api/v1/settings/llm/tests/test-created')
    const [activationUrl, activationInit] = fetch.mock.calls[6]
    expect(String(activationUrl)).toBe('/api/v1/settings/llm/configurations/created-candidate/activate')
    expect(new Headers(activationInit?.headers).get('If-Match')).toBe('"settings-4b"')
    expect(new Headers(activationInit?.headers).get('Idempotency-Key')).toMatch(/^idem-/)
    expect(JSON.parse(String(activationInit?.body))).toEqual({ test_id: 'test-created', expected_active_revision: 4 })
    expect(wrapper.text()).toContain('候选配置已激活')
    wrapper.unmount()
  })

  it('marks stale settings after a 412 refresh failure and keeps activation disabled', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
      .mockResolvedValueOnce(response({ id: 'test-1', job_id: 'job-1', resource_ref: { type: 'llm_test', id: 'test-1' }, status: 'QUEUED' }, 202, { Location: '/api/v1/settings/llm/tests/test-1' }))
      .mockResolvedValueOnce(response(testResult()))
      .mockResolvedValueOnce(errorResponse(412, 'REVISION_CONFLICT'))
      .mockResolvedValueOnce(errorResponse(503, 'SETTINGS_UNAVAILABLE', 'sensitive details'))
    const { wrapper } = mountSettings()
    await flushPromises()
    await runCandidateTest(wrapper)
    await flushPromises()
    const activate = wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))
    expect(activate?.attributes('disabled')).toBeUndefined()
    await activate?.trigger('click')
    await flushPromises()

    expect(fetch).toHaveBeenCalledTimes(6)
    expect(wrapper.text()).toContain('REVISION_CONFLICT')
    expect(wrapper.text()).toContain('无法读取最新活动版本')
    expect(wrapper.text()).toContain('当前展示的是上次读取结果，激活已禁用')
    expect(wrapper.text()).toContain('当前活动')
    expect(wrapper.text()).not.toContain('sensitive details')
    expect(wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))?.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('refreshes after a 409 and permits a guarded retry with the newly returned ETag and revision', async () => {
    const latest = settings({ active_revision: 2, candidate_configurations: [configuration({ last_test_id: 'test-1' })] })
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
      .mockResolvedValueOnce(response({ id: 'test-1', job_id: 'job-1', resource_ref: { type: 'llm_test', id: 'test-1' }, status: 'QUEUED' }, 202, { Location: '/api/v1/settings/llm/tests/test-1' }))
      .mockResolvedValueOnce(response(testResult()))
      .mockResolvedValueOnce(errorResponse(409, 'IDEMPOTENCY_CONFLICT'))
      .mockResolvedValueOnce(response(latest, 200, { ETag: '"settings-2"' }))
      .mockResolvedValueOnce(response(testResult()))
      .mockResolvedValueOnce(response(settings({ active_configuration: configuration({ status: 'ACTIVE' }), active_revision: 3, candidate_configurations: [] }), 200, { ETag: '"settings-3"' }))
    const { wrapper } = mountSettings()
    await flushPromises()
    await runCandidateTest(wrapper)
    await flushPromises()
    const activate = () => wrapper.findAll('button.primary').find((button) => button.text().includes('激活候选配置'))
    await activate()?.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('IDEMPOTENCY_CONFLICT')
    expect(wrapper.text()).toContain('已重新读取服务端当前配置')
    expect(activate()?.attributes('disabled')).toBeUndefined()
    await activate()?.trigger('click')
    await flushPromises()

    const [retryUrl, retryInit] = fetch.mock.calls[7]
    expect(String(retryUrl)).toBe('/api/v1/settings/llm/configurations/candidate-1/activate')
    expect(new Headers(retryInit?.headers).get('If-Match')).toBe('"settings-2"')
    expect(JSON.parse(String(retryInit?.body))).toEqual({ test_id: 'test-1', expected_active_revision: 2 })
    wrapper.unmount()
  })

  it('can pause and resume polling the same accepted test ID without declaring a failure', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(publicSettingsResponse())
      .mockResolvedValueOnce(response(settings(), 200, { ETag: '"settings-1"' }))
      .mockResolvedValueOnce(response({ id: 'test-1', job_id: 'job-1', resource_ref: { type: 'llm_test', id: 'test-1' }, status: 'QUEUED' }, 202, { Location: '/api/v1/settings/llm/tests/test-1' }))
      .mockResolvedValueOnce(response(testResult({ status: 'RUNNING', checks: { connectivity: 'NOT_SUPPORTED', streaming: 'NOT_SUPPORTED', tool_calling: 'NOT_SUPPORTED', structured_output: 'NOT_SUPPORTED' } })))
      .mockResolvedValueOnce(response(testResult()))
    const { wrapper } = mountSettings()
    await flushPromises()
    await runCandidateTest(wrapper)
    await flushPromises()

    expect(wrapper.text()).toContain('测试中')
    await wrapper.findAll('button').find((button) => button.text() === '停止查询')?.trigger('click')
    expect(wrapper.text()).toContain('已停止自动查询')
    expect(wrapper.text()).not.toContain('测试未通过')
    await wrapper.findAll('button').find((button) => button.text() === '继续查询')?.trigger('click')
    await flushPromises()

    expect(String(fetch.mock.calls[3][0])).toBe('/api/v1/settings/llm/tests/test-1')
    expect(String(fetch.mock.calls[4][0])).toBe('/api/v1/settings/llm/tests/test-1')
    expect(wrapper.text()).toContain('测试完成')
    wrapper.unmount()
  })

  it('clears the complete draft on cancel and clears the one-time key on unmount', async () => {
    successfulSettingsFetch()
    const { wrapper } = mountSettings()
    await flushPromises()
    await wrapper.get('input[name="configuration-name"]').setValue('草稿名')
    await wrapper.get('input[name="base-url"]').setValue('https://provider.example/v1')
    await wrapper.get('input[name="api-key-once"]').setValue('cancelled-key-marker')
    await wrapper.findAll('button').find((button) => button.text() === '清除草稿')?.trigger('click')
    expect((wrapper.get('input[name="configuration-name"]').element as HTMLInputElement).value).toBe('')
    expect((wrapper.get('input[name="base-url"]').element as HTMLInputElement).value).toBe('')
    expect(wrapper.text()).toContain('草稿已清除')

    await wrapper.get('input[name="api-key-once"]').setValue('unmounted-key-marker')
    wrapper.unmount()
  })
})
