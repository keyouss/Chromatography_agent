import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, createIdempotencyKey } from '@/core/api/client'
import type { ContractCreateConfirmationBody, ContractPreviewConfirmationBody } from '@/core/contract-types'
import type { Confirmation, ConfirmationPreview, ResourceRef } from '@/core/types'

export const useConfirmationStore = defineStore('confirmations', () => {
  const preview = ref<ConfirmationPreview | null>(null); const confirmation = ref<Confirmation | null>(null); const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle'); const error = ref<string | null>(null); const etag = ref<string | null>(null)
  let generation = 0
  function reset() { generation += 1; preview.value = null; confirmation.value = null; state.value = 'idle'; error.value = null; etag.value = null }
  const matches = (candidate: { resource_ref: ResourceRef; action: string } | null, resourceRef: ResourceRef, action: string) => Boolean(candidate && candidate.action === action && candidate.resource_ref.id === resourceRef.id && candidate.resource_ref.type === resourceRef.type)
  function previewIsFor(resourceRef: ResourceRef, action: string) { return matches(preview.value, resourceRef, action) }
  function isFor(resourceRef: ResourceRef, action: string) { return previewIsFor(resourceRef, action) && matches(confirmation.value, resourceRef, action) }
  async function loadPreview(resourceRef: ResourceRef, action: string) {
    reset(); const requestGeneration = generation; state.value = 'loading'
    try {
      const body: ContractPreviewConfirmationBody = { resource_ref: resourceRef, action }
      const loaded = await apiClient.post<ConfirmationPreview>('/confirmations/previews', body)
      if (requestGeneration === generation) { preview.value = loaded; state.value = 'ready' }
      return loaded
    } catch (reason) { if (requestGeneration === generation) { state.value = 'error'; error.value = reason instanceof Error ? reason.message : '确认预览不可用' }; throw reason }
  }
  async function create() {
    if (!preview.value || !preview.value.can_confirm) throw new Error('当前预览不可确认')
    const requestGeneration = generation; const currentPreview = preview.value
    state.value = 'loading'; error.value = null
    try {
      const body: ContractCreateConfirmationBody = { resource_ref: currentPreview.resource_ref, action: currentPreview.action, revision: currentPreview.revision, config_hash: currentPreview.config_hash, policy_version: currentPreview.policy_version }
      const result = await apiClient.requestWithMeta<Confirmation>('/confirmations', { method: 'POST', body, idempotencyKey: createIdempotencyKey(), ifMatch: currentPreview.etag })
      if (requestGeneration === generation) { confirmation.value = result.data; etag.value = result.etag ?? null; state.value = 'ready' }
      return result.data
    } catch (reason) { if (requestGeneration === generation) { state.value = 'error'; error.value = reason instanceof Error ? reason.message : '确认创建失败' }; throw reason }
  }
  return { preview, confirmation, state, error, etag, reset, previewIsFor, isFor, loadPreview, create }
})
