import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, createIdempotencyKey } from '@/core/api/client'
import type {
  ContractArchiveArtifactBody,
  ContractCompleteUploadBody,
  ContractCreateUploadBody,
} from '@/core/contract-types'
import type { Artifact, ArtifactAccepted, Upload, UploadReceipt } from '@/core/types'

/**
 * Artifact transport is intentionally a thin generated-contract adapter.
 * Scan/validation and worker completion happen on the server; this store does
 * not manufacture an available artifact when those services are unavailable.
 */
export const useArtifactStore = defineStore('artifacts', () => {
  const uploads = ref<Record<string, Upload>>({})
  const artifacts = ref<Record<string, Artifact>>({})

  async function createUpload(input: ContractCreateUploadBody) {
    const result = await apiClient.requestWithMeta<Upload>('/artifacts/uploads', {
      method: 'POST',
      body: input,
      idempotencyKey: createIdempotencyKey(),
    })
    uploads.value[result.data.id] = result.data
    return result
  }

  async function uploadContent(uploadId: string, content: Blob | ArrayBuffer | Uint8Array) {
    const result = await apiClient.requestWithMeta<UploadReceipt>(`/artifacts/uploads/${encodeURIComponent(uploadId)}/content`, {
      method: 'PUT',
      body: content,
      contentType: 'application/octet-stream',
    })
    return result
  }

  async function completeUpload(uploadId: string) {
    const body: ContractCompleteUploadBody = {}
    const result = await apiClient.requestWithMeta<ArtifactAccepted>(`/artifacts/uploads/${encodeURIComponent(uploadId)}/complete`, {
      method: 'POST',
      body,
      idempotencyKey: createIdempotencyKey(),
    })
    return result
  }

  async function getArtifact(artifactId: string) {
    const result = await apiClient.requestWithMeta<Artifact>(`/artifacts/${encodeURIComponent(artifactId)}`)
    artifacts.value[result.data.id] = result.data
    return result.data
  }

  async function downloadArtifact(artifactId: string) {
    return apiClient.requestWithMeta<Blob>(`/artifacts/${encodeURIComponent(artifactId)}/download`)
  }

  async function archiveArtifact(artifactId: string, confirmationId: string) {
    const body: ContractArchiveArtifactBody = { confirmation_id: confirmationId }
    return apiClient.requestWithMeta<Artifact>(`/artifacts/${encodeURIComponent(artifactId)}/archive`, { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
  }

  return { uploads, artifacts, createUpload, uploadContent, completeUpload, getArtifact, downloadArtifact, archiveArtifact }
})
