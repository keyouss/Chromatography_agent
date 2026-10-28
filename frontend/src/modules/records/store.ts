import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, createIdempotencyKey, type ApiResult } from '@/core/api/client'
import type { ContractArchiveRecordBody, ContractPrediction, ContractPredictionRecord } from '@/core/contract-types'

export const useRecordsStore = defineStore('prediction-records', () => {
  const records = ref<ContractPredictionRecord[]>([])
  const page = ref<{ next_cursor?: string | null; limit: number; has_more: boolean } | null>(null)
  const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const error = ref<string | null>(null)

  async function list(projectId: string | null, cursor?: string) {
    state.value = 'loading'; error.value = null
    if (!projectId) {
      state.value = 'idle'
      return null
    }
    try {
      const queryParams = new URLSearchParams({ project_id: projectId })
      if (cursor) queryParams.set('cursor', cursor)
      const query = `?${queryParams.toString()}`
      const result = await apiClient.requestWithMeta<ContractPredictionRecord[]>(`/prediction-records${query}`)
      records.value = result.data; page.value = result.page ?? null; state.value = 'ready'; return result
    } catch (reason) { state.value = 'error'; error.value = reason instanceof Error ? reason.message : '预测记录暂不可用'; throw reason }
  }

  async function get(id: string) { return apiClient.get<ContractPredictionRecord>(`/prediction-records/${encodeURIComponent(id)}`) }
  async function getPrediction(id: string) { return apiClient.get<ContractPrediction>(`/predictions/${encodeURIComponent(id)}`) }

  async function archive(id: string, confirmationId: string): Promise<ApiResult<ContractPredictionRecord>> {
    const body: ContractArchiveRecordBody = { confirmation_id: confirmationId }
    return apiClient.requestWithMeta<ContractPredictionRecord>(`/prediction-records/${encodeURIComponent(id)}/archive`, { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
  }

  return { records, page, state, error, list, get, getPrediction, archive }
})
