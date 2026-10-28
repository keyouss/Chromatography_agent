import { apiClient, createIdempotencyKey, type ApiResult } from '@/core/api/client'
import type { components } from './contracts/revision14.generated'

type CandidateSchemas = components['schemas']

export type CandidatePublicSettings = CandidateSchemas['PublicSettingsView']
export type LlmSettingsView = CandidateSchemas['LlmSettingsView']
export type LlmConfigurationView = CandidateSchemas['LlmConfigurationView']
export type LlmTestAccepted = CandidateSchemas['LlmTestAccepted']
export type LlmTestResult = CandidateSchemas['LlmTestResult']
export type LlmConfigurationActivateBody = CandidateSchemas['LlmConfigurationActivate']

type LlmConfigurationCreateSchema = CandidateSchemas['LlmConfigurationCreate']
type LlmConfigurationCredentialSource =
  | { api_key: string; reuse_secret_from_configuration_id?: null }
  | { api_key?: null; reuse_secret_from_configuration_id: string }
  | { api_key?: null; reuse_secret_from_configuration_id?: null }

export type LlmConfigurationCreateBody = Omit<LlmConfigurationCreateSchema, 'api_key' | 'reuse_secret_from_configuration_id'> & LlmConfigurationCredentialSource

type WithEtag<T> = ApiResult<T> & { etag: string }
type WithLocation<T> = ApiResult<T> & { location: string }
type WithEtagAndLocation<T> = ApiResult<T> & { etag: string; location: string }

function requireStatus<T>(result: ApiResult<T>, expected: number): ApiResult<T> {
  if (result.response.status !== expected) throw new Error(`Settings API returned unexpected HTTP ${result.response.status}; expected ${expected}`)
  return result
}

function requireEtag<T, R extends ApiResult<T>>(result: R): R & { etag: string } {
  if (!result.etag) throw new Error('Settings API response is missing the required ETag header')
  return result as R & { etag: string }
}

function requireLocation<T, R extends ApiResult<T>>(result: R): R & { location: string } {
  if (!result.location?.trim()) throw new Error('Settings API response is missing the required Location header')
  return result as R & { location: string }
}

function requireIdempotencyKey(key: string): string {
  if (!key.trim()) throw new Error('Idempotency-Key must not be empty')
  return key
}

export async function getPublicSettings(): Promise<ApiResult<CandidatePublicSettings>> {
  return requireStatus(await apiClient.requestWithMeta<CandidatePublicSettings>('/settings/public'), 200)
}

export async function getLlmSettings(): Promise<WithEtag<LlmSettingsView>> {
  const result = requireStatus(await apiClient.requestWithMeta<LlmSettingsView>('/settings/llm'), 200)
  return requireEtag(result)
}

export async function createLlmConfiguration(
  body: LlmConfigurationCreateBody,
  idempotencyKey = createIdempotencyKey(),
): Promise<WithEtagAndLocation<LlmConfigurationView>> {
  if (body.api_key != null && body.reuse_secret_from_configuration_id != null) {
    throw new Error('API Key and existing secret reference are mutually exclusive')
  }
  const result = requireStatus(await apiClient.requestWithMeta<LlmConfigurationView>('/settings/llm/configurations', {
    method: 'POST',
    body,
    idempotencyKey: requireIdempotencyKey(idempotencyKey),
  }), 201)
  return requireLocation(requireEtag(result))
}

export async function submitLlmConfigurationTest(
  configurationId: string,
  idempotencyKey = createIdempotencyKey(),
): Promise<WithLocation<LlmTestAccepted>> {
  if (!configurationId.trim()) throw new Error('Configuration ID must not be empty')
  const body: CandidateSchemas['LlmConfigurationTestRequest'] = {}
  const result = requireStatus(await apiClient.requestWithMeta<LlmTestAccepted>(`/settings/llm/configurations/${encodeURIComponent(configurationId)}/test`, {
    method: 'POST',
    body,
    idempotencyKey: requireIdempotencyKey(idempotencyKey),
  }), 202)
  return requireLocation(result)
}

export async function getLlmTest(testId: string, signal?: AbortSignal): Promise<ApiResult<LlmTestResult>> {
  if (!testId.trim()) throw new Error('Test ID must not be empty')
  return requireStatus(await apiClient.requestWithMeta<LlmTestResult>(`/settings/llm/tests/${encodeURIComponent(testId)}`, { signal }), 200)
}

export async function activateLlmConfiguration(
  configurationId: string,
  body: LlmConfigurationActivateBody,
  etag: string,
  idempotencyKey = createIdempotencyKey(),
): Promise<WithEtag<LlmSettingsView>> {
  if (!configurationId.trim()) throw new Error('Configuration ID must not be empty')
  if (!etag.trim()) throw new Error('If-Match must not be empty')
  if (!body.test_id.trim() || !Number.isInteger(body.expected_active_revision) || body.expected_active_revision < 0) {
    throw new Error('Activation requires a test ID and a non-negative active revision')
  }
  const result = requireStatus(await apiClient.requestWithMeta<LlmSettingsView>(`/settings/llm/configurations/${encodeURIComponent(configurationId)}/activate`, {
    method: 'POST',
    body,
    ifMatch: etag,
    idempotencyKey: requireIdempotencyKey(idempotencyKey),
  }), 200)
  return requireEtag(result)
}
