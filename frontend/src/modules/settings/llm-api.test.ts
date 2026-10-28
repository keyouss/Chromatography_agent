import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClientError } from '@/core/api/client'
import type { components } from './contracts/revision14.generated'
import {
  activateLlmConfiguration,
  createLlmConfiguration,
  getLlmSettings,
  getLlmTest,
  getPublicSettings,
  submitLlmConfigurationTest,
} from './llm-api'

function response(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ data, request_id: 'candidate-settings-test', warnings: [] }), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  })
}

function configuration(overrides: Partial<components['schemas']['LlmConfigurationView']> = {}): components['schemas']['LlmConfigurationView'] {
  return {
    id: 'configuration-1', name: 'Test config', revision: '1', etag: '"config-1"', api_format: 'openai_chat',
    base_url: 'https://provider.example/v1', model_id: 'model-test', has_api_key: true, status: 'CANDIDATE',
    limits: { timeout_seconds: 90, max_retries: 2, max_output_tokens: 4096 },
    external_send_policy: 'deny_external', created_at: '2026-10-06T00:00:00Z', ...overrides,
  }
}

const createBodyDefaults = {
  api_format: 'openai_chat',
  external_send_policy: 'deny_external',
  max_output_tokens: 4096,
  max_retries: 2,
  timeout_seconds: 90,
} satisfies Pick<import('./llm-api').LlmConfigurationCreateBody, 'api_format' | 'external_send_policy' | 'max_output_tokens' | 'max_retries' | 'timeout_seconds'>

afterEach(() => vi.restoreAllMocks())

describe('candidate revision14 Settings API adapter', () => {
  it('reads public and LLM settings through the shared /api/v1 client and retains the LLM ETag', async () => {
    const llmSettings: components['schemas']['LlmSettingsView'] = { active_revision: 0, candidate_configurations: [] }
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response({ product_name: 'Chromatography Platform', build_id: 'build-test', input_limits: {}, export_limits: {}, allowed_modes: ['business'], built_modules: [] }))
      .mockResolvedValueOnce(response(llmSettings, 200, { ETag: '"settings-0"' }))

    const publicSettings = await getPublicSettings()
    const settings = await getLlmSettings()

    expect(fetch.mock.calls.map(([url]) => String(url))).toEqual(['/api/v1/settings/public', '/api/v1/settings/llm'])
    expect(publicSettings.data.product_name).toBe('Chromatography Platform')
    expect(settings.data.active_revision).toBe(0)
    expect(settings.etag).toBe('"settings-0"')
  })

  it('creates a candidate with exactly one credential source and captures declared response headers', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(configuration(), 201, { ETag: '"candidate-1"', Location: '/api/v1/settings/llm/configurations/configuration-1' }))
      .mockResolvedValueOnce(response(configuration({ id: 'configuration-2' }), 201, { ETag: '"candidate-2"', Location: '/api/v1/settings/llm/configurations/configuration-2' }))
    const body = {
      ...createBodyDefaults,
      name: 'Test config', base_url: 'https://provider.example/v1', model_id: 'model-test', api_key: 'test-only-marker',
    } as const
    const reuseBody = {
      ...createBodyDefaults,
      name: 'Reused config', base_url: 'https://provider.example/v1', model_id: 'model-test', reuse_secret_from_configuration_id: 'configuration-old',
    } as const

    const result = await createLlmConfiguration(body, 'idem-create-test')
    const reused = await createLlmConfiguration(reuseBody, 'idem-reuse-test')

    const [url, init] = fetch.mock.calls[0]
    expect(String(url)).toBe('/api/v1/settings/llm/configurations')
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('idem-create-test')
    expect(JSON.parse(String(init?.body))).toEqual(body)
    expect(result.response.status).toBe(201)
    expect(result.etag).toBe('"candidate-1"')
    expect(result.location).toBe('/api/v1/settings/llm/configurations/configuration-1')
    expect(new Headers(fetch.mock.calls[1][1]?.headers).get('Idempotency-Key')).toBe('idem-reuse-test')
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual(reuseBody)
    expect(reused.data.id).toBe('configuration-2')
  })

  it('submits an empty test request with idempotency, then polls the accepted test resource', async () => {
    const accepted: components['schemas']['LlmTestAccepted'] = {
      id: 'test-1', job_id: 'job-1', resource_ref: { type: 'llm_test', id: 'test-1' }, status: 'QUEUED',
    }
    const testResult: components['schemas']['LlmTestResult'] = {
      id: 'test-1', configuration_id: 'configuration-1', configuration_revision: '1', status: 'RUNNING',
      checks: { connectivity: 'PASSED', streaming: 'NOT_SUPPORTED', tool_calling: 'NOT_SUPPORTED', structured_output: 'NOT_SUPPORTED' },
      created_at: '2026-10-06T00:00:00Z',
    }
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(accepted, 202, { Location: '/api/v1/settings/llm/tests/test-1' }))
      .mockResolvedValueOnce(response(testResult))

    const submitted = await submitLlmConfigurationTest('configuration/1', 'idem-test-submit')
    const polled = await getLlmTest(submitted.data.id)

    const [submitUrl, submitInit] = fetch.mock.calls[0]
    expect(String(submitUrl)).toBe('/api/v1/settings/llm/configurations/configuration%2F1/test')
    expect(new Headers(submitInit?.headers).get('Idempotency-Key')).toBe('idem-test-submit')
    expect(JSON.parse(String(submitInit?.body))).toEqual({})
    expect(submitted.response.status).toBe(202)
    expect(submitted.location).toBe('/api/v1/settings/llm/tests/test-1')
    expect(String(fetch.mock.calls[1][0])).toBe('/api/v1/settings/llm/tests/test-1')
    expect(polled.data.status).toBe('RUNNING')
  })

  it('activates with the expected revision, If-Match, and idempotency key while preserving API conflicts', async () => {
    const settings: components['schemas']['LlmSettingsView'] = { active_configuration: configuration({ status: 'ACTIVE' }), active_revision: 2, candidate_configurations: [] }
    const fetch = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(response(settings, 200, { ETag: '"settings-2"' }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'REVISION_CONFLICT', message: '活动配置已更新', field_errors: [] }, request_id: 'conflict-test' }), {
        status: 412, headers: { 'content-type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: { code: 'IDEMPOTENCY_CONFLICT', message: '幂等键已被其它请求使用', field_errors: [] }, request_id: 'conflict-test' }), {
        status: 409, headers: { 'content-type': 'application/json' },
      }))
    const body: components['schemas']['LlmConfigurationActivate'] = { test_id: 'test-1', expected_active_revision: 1 }

    const activated = await activateLlmConfiguration('configuration/1', body, '"settings-1"', 'idem-activate-test')
    const [url, init] = fetch.mock.calls[0]
    expect(String(url)).toBe('/api/v1/settings/llm/configurations/configuration%2F1/activate')
    expect(new Headers(init?.headers).get('If-Match')).toBe('"settings-1"')
    expect(new Headers(init?.headers).get('Idempotency-Key')).toBe('idem-activate-test')
    expect(JSON.parse(String(init?.body))).toEqual(body)
    expect(activated.etag).toBe('"settings-2"')

    const conflict = activateLlmConfiguration('configuration-1', body, '"settings-1"', 'idem-activate-conflict')
    await expect(conflict).rejects.toBeInstanceOf(ApiClientError)
    await expect(conflict).rejects.toMatchObject({ status: 412, code: 'REVISION_CONFLICT' })
    await expect(createLlmConfiguration({ ...createBodyDefaults, name: 'Test config', base_url: 'https://provider.example/v1', model_id: 'model-test' }, 'idem-conflict'))
      .rejects.toMatchObject({ status: 409, code: 'IDEMPOTENCY_CONFLICT' })
  })

  it('rejects invalid credentials, missing preconditions, and incomplete success headers without sending false success', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(response(configuration(), 201, { ETag: '"candidate-1"' }))
    const conflictingBody = {
      ...createBodyDefaults,
      name: 'Test config', base_url: 'https://provider.example/v1', model_id: 'model-test',
      api_key: 'test-only-marker',
    } satisfies import('./llm-api').LlmConfigurationCreateBody
    Object.assign(conflictingBody, { reuse_secret_from_configuration_id: 'configuration-old' })

    await expect(createLlmConfiguration(conflictingBody, 'idem-invalid')).rejects.toThrow('mutually exclusive')
    await expect(activateLlmConfiguration('configuration-1', { test_id: 'test-1', expected_active_revision: 0 }, '', 'idem-no-etag'))
      .rejects.toThrow('If-Match')
    await expect(createLlmConfiguration({ ...createBodyDefaults, name: 'Test config', base_url: 'https://provider.example/v1', model_id: 'model-test' }, 'idem-missing-header'))
      .rejects.toThrow('required Location')
    expect(fetch).toHaveBeenCalledTimes(1)
  })
})
