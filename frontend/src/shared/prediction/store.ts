import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, createIdempotencyKey, type ApiResult } from '@/core/api/client'
import type {
  ContractArchiveRecordBody,
  ContractCreatePredictionOperation,
  ContractPrediction,
  ContractPredictionAccepted,
  ContractPredictionCreateBody,
  ContractPredictionDraft,
  ContractPredictionRecord,
  ContractPredictionRecordListEnvelope,
  ContractSfcCandidateScope,
  ContractSfcCandidateScopeCreateBody,
  ContractSfcCandidateScopePreview,
  ContractSfcCandidateScopePreviewBody,
  ContractSfcConditionsOptions,
  ContractTlcConditionComparisonBody,
  ContractTlcConditionOptions,
  ContractTlcReverseBody,
  ContractToolCapability,
  ContractTypeCapability,
  ContractTypeSummary,
} from '@/core/contract-types'
import type { Hplc11Accepted, Hplc11CreateBody, Hplc11Prediction } from './hplc11'
import type { Gc11Accepted, Gc11CreateBody, Gc11Prediction } from './gc11'
import type { Sfc11FullBody, Sfc11Options, Sfc11Scope, Sfc11ScopeCreate, Sfc11SingleBody } from './sfc11'
import type { components as Sfc11Components } from './contracts/sfc-v11-exact-revision20261008-02.generated'

type PredictionBindingValidator = (prediction: ContractPrediction) => boolean | Promise<boolean>

export function firstAvailablePredictionType(types: readonly Pick<ContractTypeSummary, 'id' | 'health'>[]): string {
  return types.find((item) => item.health.status === 'READY')?.id ?? types.find((item) => item.health.status !== 'UNAVAILABLE')?.id ?? types[0]?.id ?? ''
}

export function unavailableCapabilityReason(health: Pick<ContractTypeSummary, 'health'>['health'] | null | undefined): string | null {
  return health?.status === 'UNAVAILABLE' ? health.reason ?? '服务端能力不可用' : null
}

export const usePredictionStore = defineStore('prediction', () => {
  const types = ref<ContractTypeSummary[]>([])
  const typeCapability = ref<ContractTypeCapability | null>(null)
  const toolSchema = ref<ContractToolCapability | null>(null)
  const tlcOptions = ref<ContractTlcConditionOptions | null>(null)
  const sfcCandidateScope = ref<ContractSfcCandidateScope | null>(null)
  const sfcOptions = ref<ContractSfcConditionsOptions | null>(null)
  const records = ref<ContractPredictionRecord[]>([])
  const prediction = ref<ContractPrediction | null>(null)
  const accepted = ref<ContractPredictionAccepted | null>(null)
  const draft = ref<ContractPredictionDraft | null>(null)
  const page = ref<ContractPredictionRecordListEnvelope['page'] | null>(null)
  const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const error = ref<string | null>(null)
  const selectedType = ref('')
  const selectedTool = ref('')
  const availableTypes = computed(() => types.value)

  async function loadTypes(isCurrent: () => boolean = () => true) {
    state.value = 'loading'; error.value = null
    try {
      const result = await apiClient.get<ContractTypeSummary[]>('/prediction/types')
      if (!isCurrent()) return result
      types.value = result
      if (!selectedType.value || !result.some((item) => item.id === selectedType.value)) {
        selectedType.value = firstAvailablePredictionType(result)
      }
      if (!selectedTool.value) selectedTool.value = result.find((item) => item.id === selectedType.value)?.tools[0] ?? ''
      state.value = 'ready'; return result
    } catch (reason) { if (isCurrent()) { state.value = 'error'; error.value = reason instanceof Error ? reason.message : '预测能力暂不可用' }; throw reason }
  }

  async function loadType(type: string, isCurrent: () => boolean = () => true) {
    if (isCurrent()) selectedType.value = type
    const loaded = await apiClient.get<ContractTypeCapability>(`/prediction/types/${encodeURIComponent(type)}`)
    if (isCurrent()) typeCapability.value = loaded
    return loaded
  }

  async function loadTool(type: string, tool: string, schemaVersion?: string, isCurrent: () => boolean = () => true) {
    if (isCurrent()) { selectedType.value = type; selectedTool.value = tool }
    const query = schemaVersion ? `?schema_version=${encodeURIComponent(schemaVersion)}` : ''
    const loaded = await apiClient.get<ContractToolCapability>(`/prediction/types/${encodeURIComponent(type)}/tools/${encodeURIComponent(tool)}/schema${query}`)
    if (isCurrent()) toolSchema.value = loaded
    return loaded
  }

  async function loadTlcOptions(isCurrent: () => boolean = () => true) {
    const loaded = await apiClient.get<ContractTlcConditionOptions>('/tlc/conditions/options')
    if (isCurrent()) tlcOptions.value = loaded
    return loaded
  }

  async function createTlcComparison(body: ContractTlcConditionComparisonBody, isCurrent: () => boolean = () => true): Promise<ApiResult<ContractPredictionAccepted>> {
    const result = await apiClient.requestWithMeta<ContractPredictionAccepted>('/tlc/condition-comparisons', { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
    if (isCurrent()) accepted.value = result.data
    return result
  }

  async function createTlcReverse(body: ContractTlcReverseBody, isCurrent: () => boolean = () => true): Promise<ApiResult<ContractPredictionAccepted>> {
    const result = await apiClient.requestWithMeta<ContractPredictionAccepted>('/tlc/reverse-solvent', { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
    if (isCurrent()) accepted.value = result.data
    return result
  }

  async function createSfcCandidateScope(body: ContractSfcCandidateScopeCreateBody, isCurrent: () => boolean = () => true) {
    const result = await apiClient.requestWithMeta<ContractSfcCandidateScope>('/sfc/candidate-scopes', { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
    if (isCurrent()) sfcCandidateScope.value = result.data
    return result
  }

  async function loadSfcOptions(scopeId?: string, projectId?: string, isCurrent: () => boolean = () => true, persist = true) {
    if (persist && isCurrent()) sfcOptions.value = null
    const query = new URLSearchParams()
    if (scopeId) query.set('scope_id', scopeId)
    if (projectId) query.set('project_id', projectId)
    try {
      const result = await apiClient.requestWithMeta<ContractSfcConditionsOptions>(`/sfc/conditions/options${query.toString() ? `?${query.toString()}` : ''}`)
      if (persist && isCurrent()) sfcOptions.value = result.data
      return result
    } catch (reason) {
      if (persist && isCurrent()) sfcOptions.value = null
      throw reason
    }
  }

  /** Candidate SFC 1.1 path; query identity is either scope_id or exact model pair. */
  async function loadSfc11Options(query: { project_id: string; scope_id: string; model_id?: never; model_version?: never } | { project_id: string; scope_id?: never; model_id: string; model_version: string }) {
    const scopeProvided = query.scope_id !== undefined
    const modelIdProvided = query.model_id !== undefined
    const modelVersionProvided = query.model_version !== undefined
    const hasScope = typeof query.scope_id === 'string' && Boolean(query.scope_id.trim())
    const hasModelId = typeof query.model_id === 'string' && Boolean(query.model_id.trim())
    const hasModelVersion = typeof query.model_version === 'string' && Boolean(query.model_version.trim())
    const scopeMode = scopeProvided && !modelIdProvided && !modelVersionProvided && hasScope
    const modelMode = !scopeProvided && modelIdProvided && modelVersionProvided && hasModelId && hasModelVersion
    if (typeof query.project_id !== 'string' || !query.project_id.trim() || !(scopeMode || modelMode)) {
      throw new Error('SFC options 查询必须且只能使用 scope_id 或完整 model_id/model_version。')
    }
    const params = new URLSearchParams({ project_id: query.project_id })
    if (scopeMode) params.set('scope_id', query.scope_id!)
    else { params.set('model_id', query.model_id!); params.set('model_version', query.model_version!) }
    return apiClient.requestWithMeta<Sfc11Options>(`/sfc/conditions/options?${params.toString()}`)
  }

  async function createSfc11CandidateScope(body: Sfc11ScopeCreate) {
    return apiClient.requestWithMeta<Sfc11Scope>('/sfc/candidate-scopes', { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
  }

  async function previewSfc11CandidateScope(body: ContractSfcCandidateScopePreviewBody) {
    return apiClient.requestWithMeta<ContractSfcCandidateScopePreview>('/sfc/candidate-scopes/preview', { method: 'POST', body })
  }

  async function createSfc11(body: Sfc11SingleBody | Sfc11FullBody, isCurrent: () => boolean = () => true) {
    const result = await apiClient.requestWithMeta<Sfc11Components['schemas']['PredictionAccepted']>('/predictions', {
      method: 'POST', body, idempotencyKey: createIdempotencyKey(),
    })
    if (isCurrent()) accepted.value = result.data as unknown as ContractPredictionAccepted
    return result
  }

  async function create(input: ContractPredictionCreateBody, isCurrent: () => boolean = () => true): Promise<ApiResult<ContractPredictionAccepted>> {
    const result = await apiClient.requestWithMeta<ContractPredictionAccepted>('/predictions', { method: 'POST', body: input, idempotencyKey: createIdempotencyKey() })
    if (isCurrent()) accepted.value = result.data
    return result
  }

  async function createHplc11(input: Hplc11CreateBody, isCurrent: () => boolean = () => true): Promise<ApiResult<Hplc11Accepted>> {
    const result = await apiClient.requestWithMeta<Hplc11Accepted>('/predictions', { method: 'POST', body: input, idempotencyKey: createIdempotencyKey() })
    if (isCurrent()) accepted.value = result.data as unknown as ContractPredictionAccepted
    return result
  }

  async function createGc11(input: Gc11CreateBody, isCurrent: () => boolean = () => true): Promise<ApiResult<Gc11Accepted>> {
    const result = await apiClient.requestWithMeta<Gc11Accepted>('/predictions', { method: 'POST', body: input, idempotencyKey: createIdempotencyKey() })
    if (isCurrent()) accepted.value = result.data as unknown as ContractPredictionAccepted
    return result
  }

  async function getPrediction(id: string, isCurrent: () => boolean = () => true, expectedProjectId?: string, expectedType?: string, validateBinding?: PredictionBindingValidator) {
    const loaded = expectedType === 'hplc'
      ? await apiClient.get<Hplc11Prediction>(`/predictions/${encodeURIComponent(id)}`) as unknown as ContractPrediction
      : expectedType === 'gc'
        ? await apiClient.get<Gc11Prediction>(`/predictions/${encodeURIComponent(id)}`) as unknown as ContractPrediction
        : await apiClient.get<ContractPrediction>(`/predictions/${encodeURIComponent(id)}`)
    if (!isCurrent()) return loaded
    const identityMatches = (!expectedProjectId || loaded.project_id === expectedProjectId) && (!expectedType || loaded.chromatography_type === expectedType)
    let bindingMatches = identityMatches
    try { if (bindingMatches && validateBinding) bindingMatches = await validateBinding(loaded) }
    catch (reason) {
      if (isCurrent() && validateBinding) { prediction.value = null; accepted.value = null }
      throw reason
    }
    if (!isCurrent()) return loaded
    if (bindingMatches) prediction.value = loaded
    else {
      prediction.value = null
      accepted.value = null
      if (validateBinding) throw new Error('Prediction binding mismatch')
    }
    return loaded
  }

  async function listRecords(cursor?: string) {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
    const result = await apiClient.requestWithMeta<ContractPredictionRecord[]>(`/prediction-records${query}`)
    records.value = result.data; page.value = result.page ?? null; return result
  }

  async function getRecord(id: string) {
    const result = await apiClient.get<ContractPredictionRecord>(`/prediction-records/${encodeURIComponent(id)}`)
    records.value = [...records.value.filter((item) => item.id !== id), result]
    return result
  }

  async function archiveRecord(id: string, confirmationId: string) {
    const body: ContractArchiveRecordBody = { confirmation_id: confirmationId }
    return apiClient.requestWithMeta<ContractPredictionRecord>(`/prediction-records/${encodeURIComponent(id)}/archive`, { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
  }

  async function replayRecord(id: string) {
    const result = await apiClient.requestWithMeta<ContractPredictionDraft>(`/prediction-records/${encodeURIComponent(id)}/replay-draft`, { method: 'POST', body: {}, idempotencyKey: createIdempotencyKey() })
    draft.value = result.data; return result
  }

  return { types, availableTypes, typeCapability, toolSchema, tlcOptions, sfcCandidateScope, sfcOptions, records, prediction, accepted, draft, page, state, error, selectedType, selectedTool, loadTypes, loadType, loadTool, loadTlcOptions, createTlcComparison, createTlcReverse, createSfcCandidateScope, loadSfcOptions, loadSfc11Options, createSfc11CandidateScope, previewSfc11CandidateScope, createSfc11, create, createHplc11, createGc11, getPrediction, listRecords, getRecord, archiveRecord, replayRecord }
})

export type PredictionCreateOperation = ContractCreatePredictionOperation
