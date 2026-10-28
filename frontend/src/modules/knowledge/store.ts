import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, createIdempotencyKey } from '@/core/api/client'
import type {
  ContractCitation,
  ContractKnowledgeDocument,
  ContractKnowledgeDocumentCreateBody,
  ContractKnowledgeDocumentListEnvelope,
  ContractKnowledgeAccepted,
  ContractKnowledgeArchiveBody,
  ContractKnowledgeEmptyBody,
  ContractKnowledgeSearchBody,
  ContractKnowledgeSearchResult,
} from '@/core/contract-types'

export const useKnowledgeStore = defineStore('knowledge', () => {
  const documents = ref<ContractKnowledgeDocument[]>([])
  const documentPage = ref<ContractKnowledgeDocumentListEnvelope['page'] | null>(null)
  const search = ref<ContractKnowledgeSearchResult | null>(null)
  const selectedCitation = ref<ContractCitation | null>(null)
  const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const error = ref<string | null>(null)
  const location = ref<string | null>(null)

  async function listDocuments(projectId: string, cursor?: string) {
    state.value = 'loading'; error.value = null
    try {
      const query = `?project_id=${encodeURIComponent(projectId)}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ''}`
      const result = await apiClient.requestWithMeta<ContractKnowledgeDocument[]>(`/knowledge/documents${query}`)
      documents.value = result.data; documentPage.value = result.page ?? null; state.value = 'ready'; return result
    } catch (reason) { state.value = 'error'; error.value = reason instanceof Error ? reason.message : '知识文档暂不可用'; throw reason }
  }

  async function searchKnowledge(body: ContractKnowledgeSearchBody) {
    state.value = 'loading'; error.value = null
    try { const result = await apiClient.requestWithMeta<ContractKnowledgeSearchResult>('/knowledge/search', { method: 'POST', body }); search.value = result.data; state.value = 'ready'; return result }
    catch (reason) { state.value = 'error'; error.value = reason instanceof Error ? reason.message : '知识检索暂不可用'; throw reason }
  }

  async function getCitation(citationId: string, projectId: string) {
    const result = await apiClient.requestWithMeta<ContractCitation>(`/knowledge/citations/${encodeURIComponent(citationId)}?project_id=${encodeURIComponent(projectId)}`)
    selectedCitation.value = result.data; return result
  }

  async function createDocument(body: ContractKnowledgeDocumentCreateBody) {
    const result = await apiClient.requestWithMeta<ContractKnowledgeDocument>('/knowledge/documents', { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
    location.value = result.location ?? null; documents.value = [result.data, ...documents.value]; return result
  }
  async function indexDocument(id: string, projectId: string, etag: string) { const body: ContractKnowledgeEmptyBody = {}; const result = await apiClient.requestWithMeta<ContractKnowledgeAccepted>(`/knowledge/documents/${encodeURIComponent(id)}/index?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body, ifMatch: etag, idempotencyKey: createIdempotencyKey() }); location.value = result.location ?? null; return result }
  async function archiveDocument(id: string, projectId: string, confirmationId: string) { const body: ContractKnowledgeArchiveBody = { confirmation_id: confirmationId }; const result = await apiClient.requestWithMeta<ContractKnowledgeDocument>(`/knowledge/documents/${encodeURIComponent(id)}/archive?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body, idempotencyKey: createIdempotencyKey() }); location.value = result.location ?? null; return result }

  return { documents, documentPage, search, selectedCitation, state, error, location, listDocuments, searchKnowledge, getCitation, createDocument, indexDocument, archiveDocument }
})
