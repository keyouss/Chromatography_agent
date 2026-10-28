import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, createIdempotencyKey, type ApiResult } from '@/core/api/client'
import type { ContractExperiment, ContractExperimentAccepted, ContractExperimentCreateBody, ContractExperimentValidation, ContractListedEvaluationProtocol, ContractSubmitRequestBody, ContractTrainingEmptyBody, ContractTrainingTemplate } from '@/core/contract-types'

export const useTrainingStore = defineStore('training', () => {
  const templates = ref<ContractTrainingTemplate[]>([])
  const protocols = ref<ContractListedEvaluationProtocol[]>([])
  const experiments = ref<ContractExperiment[]>([])
  const selected = ref<ContractExperiment | null>(null)
  const validation = ref<ContractExperimentValidation | null>(null)
  const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const error = ref<string | null>(null)
  const location = ref<string | null>(null)

  async function listTemplates() { const result = await apiClient.requestWithMeta<ContractTrainingTemplate[]>('/training/templates'); templates.value = result.data; return result }
  async function listProtocols(taskType?: string) { const result = await apiClient.requestWithMeta<ContractListedEvaluationProtocol[]>(`/evaluation-protocols${taskType ? `?task_type=${encodeURIComponent(taskType)}` : ''}`); protocols.value = result.data; return result }
  async function listExperiments(projectId: string, cursor?: string) { state.value = 'loading'; error.value = null; try { const result = await apiClient.requestWithMeta<ContractExperiment[]>(`/training/experiments?project_id=${encodeURIComponent(projectId)}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`); experiments.value = result.data; state.value = 'ready'; return result } catch (reason) { state.value = 'error'; error.value = reason instanceof Error ? reason.message : '训练实验暂不可用'; throw reason } }
  async function getExperiment(id: string, projectId: string) { const result = await apiClient.requestWithMeta<ContractExperiment>(`/training/experiments/${encodeURIComponent(id)}?project_id=${encodeURIComponent(projectId)}`); selected.value = result.data; return result }
  async function createExperiment(body: ContractExperimentCreateBody) { const result = await apiClient.requestWithMeta<ContractExperiment>('/training/experiments', { method: 'POST', body, idempotencyKey: createIdempotencyKey() }); selected.value = result.data; experiments.value = [result.data, ...experiments.value]; return result }
  async function validateExperiment(id: string, projectId: string, etag: string): Promise<ApiResult<ContractExperimentValidation>> { const body: ContractTrainingEmptyBody = {}; const result = await apiClient.requestWithMeta<ContractExperimentValidation>(`/training/experiments/${encodeURIComponent(id)}/validate?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body, ifMatch: etag, idempotencyKey: createIdempotencyKey() }); validation.value = result.data; return result }
  async function submitExperiment(id: string, projectId: string, confirmationId: string): Promise<ApiResult<ContractExperimentAccepted>> { const body: ContractSubmitRequestBody = { confirmation_id: confirmationId }; const result = await apiClient.requestWithMeta<ContractExperimentAccepted>(`/training/experiments/${encodeURIComponent(id)}/submit?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body, idempotencyKey: createIdempotencyKey() }); location.value = result.location ?? null; return result }
  return { templates, protocols, experiments, selected, validation, state, error, location, listTemplates, listProtocols, listExperiments, getExperiment, createExperiment, validateExperiment, submitExperiment }
})
