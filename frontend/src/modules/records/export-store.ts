import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ApiClientError, apiClient, createIdempotencyKey } from '@/core/api/client'
import type { ContractCancelJobBody, ContractCreateRecordExportBody, ContractExportAccepted, ContractExportView, ContractJob, ContractRetryJobBody } from '@/core/contract-types'
import type { Artifact } from '@/core/types'
import { useArtifactStore } from '@/stores/artifacts'

const STORAGE_KEY = 'chrom-platform.record-export'
type PendingExport = { body: ContractCreateRecordExportBody; idempotencyKey: string; fingerprint: string }
type SavedExport = { exportId?: string; pending?: PendingExport }
const TERMINAL_JOB_STATUSES = new Set<ContractJob['status']>(['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'])
const EXPORT_STATUSES = new Set(['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'])

function isRecord(value: unknown): value is Record<string, unknown> { return Boolean(value && typeof value === 'object' && !Array.isArray(value)) }

function isExportView(value: unknown): value is ContractExportView {
  if (!isRecord(value) || Object.keys(value).some((key) => !['artifact_id', 'completed_at', 'created_at', 'error', 'format', 'format_version', 'id', 'job_id', 'source_snapshots', 'status'].includes(key))) return false
  const date = (item: unknown) => typeof item === 'string' && !Number.isNaN(Date.parse(item))
  return typeof value.id === 'string' && value.id.length > 0 && typeof value.job_id === 'string' && value.job_id.length > 0
    && typeof value.status === 'string' && EXPORT_STATUSES.has(value.status)
    && ['json', 'csv', 'xlsx'].includes(String(value.format)) && typeof value.format_version === 'string' && value.format_version.length > 0
    && isRecord(value.source_snapshots) && date(value.created_at)
    && (value.artifact_id === undefined || value.artifact_id === null || typeof value.artifact_id === 'string')
    && (value.completed_at === undefined || value.completed_at === null || date(value.completed_at))
    && (value.error === undefined || value.error === null || isRecord(value.error))
}

function isExportAccepted(value: unknown): value is ContractExportAccepted {
  if (!isRecord(value) || Object.keys(value).some((key) => !['id', 'job_id', 'resource_ref', 'status'].includes(key))) return false
  return typeof value.id === 'string' && value.id.length > 0 && typeof value.job_id === 'string' && value.job_id.length > 0
    && typeof value.status === 'string' && ['QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED'].includes(value.status)
    && isRecord(value.resource_ref) && value.resource_ref.type === 'export' && value.resource_ref.id === value.id
}

function isExportCreateBody(value: unknown): value is ContractCreateRecordExportBody {
  if (!isRecord(value) || Object.keys(value).some((key) => !['project_id', 'resource_refs', 'format', 'include'].includes(key))) return false
  return typeof value.project_id === 'string' && value.project_id.length > 0
    && ['json', 'csv', 'xlsx'].includes(String(value.format))
    && Array.isArray(value.resource_refs) && value.resource_refs.length > 0 && value.resource_refs.every((ref) => isRecord(ref) && ref.type === 'prediction_record' && typeof ref.id === 'string' && ref.id.length > 0)
    && Array.isArray(value.include) && value.include.length > 0 && value.include.every((item) => ['input_snapshot', 'result', 'model_version', 'warnings'].includes(String(item)))
}

function isExpectedExportLocation(value: string, exportId: string): boolean {
  if (/[?#]/.test(value)) return false
  try {
    const requestUrl = new URL('/api/v1/exports', window.location.origin)
    const location = new URL(value, requestUrl)
    return location.origin === requestUrl.origin && !location.username && !location.password
      && !location.search && !location.hash
      && location.pathname === `/api/v1/exports/${encodeURIComponent(exportId)}`
  } catch { return false }
}

function storageKey(projectId: string) { return `${STORAGE_KEY}:${encodeURIComponent(projectId)}` }

function readSavedExport(projectId: string): SavedExport | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey(projectId)) ?? 'null') as Partial<SavedExport> | null
    if (typeof value?.exportId === 'string') return { exportId: value.exportId }
    const pending = value?.pending
    const body = pending?.body
    const validRefs = Array.isArray(body?.resource_refs) && body.resource_refs.length > 0 && body.resource_refs.every((item) => item.type === 'prediction_record' && typeof item.id === 'string' && item.id.length > 0)
    const validIncludes = Array.isArray(body?.include) && body.include.length > 0 && body.include.every((item) => ['input_snapshot', 'result', 'model_version', 'warnings'].includes(item))
    if (pending && typeof pending.idempotencyKey === 'string' && pending.idempotencyKey.length > 0 && body?.project_id === projectId && ['json', 'csv', 'xlsx'].includes(body.format) && validRefs && validIncludes && pending.fingerprint === JSON.stringify(body)) return { pending: pending as PendingExport }
    return null
  } catch { return null }
}

function saveExport(projectId: string, value: SavedExport | null) {
  try {
    if (value) sessionStorage.setItem(storageKey(projectId), JSON.stringify(value))
    else sessionStorage.removeItem(storageKey(projectId))
  } catch { /* session recovery is optional when browser storage is unavailable */ }
}

export const useRecordExportStore = defineStore('record-export', () => {
  const accepted = ref<ContractExportAccepted | null>(null)
  const exportView = ref<ContractExportView | null>(null)
  const job = ref<ContractJob | null>(null)
  const artifact = ref<Artifact | null>(null)
  const projectId = ref<string | null>(null)
  const recoveredExportId = ref<string | null>(null)
  const pendingSubmission = ref<PendingExport | null>(null)
  const state = ref<'idle' | 'loading' | 'submitting' | 'ready' | 'error'>('idle')
  const error = ref<string | null>(null)
  const artifactError = ref<string | null>(null)
  const busy = ref(false)
  const accessDenied = ref(false)
  const exportId = computed(() => exportView.value?.id ?? accepted.value?.id ?? recoveredExportId.value)
  const canCancel = computed(() => Boolean(state.value === 'ready' && !accessDenied.value && job.value?.cancelable && !TERMINAL_JOB_STATUSES.has(job.value.status)))
  const canRetry = computed(() => Boolean(state.value === 'ready' && !accessDenied.value && job.value?.retryable && job.value.status !== 'SUCCEEDED'))
  const isPending = computed(() => {
    const status = exportView.value?.status ?? accepted.value?.status
    return status === 'QUEUED' || status === 'RUNNING' || job.value?.status === 'CANCEL_REQUESTED'
  })
  function isCurrent(project: string, id: string) { return projectId.value === project && exportId.value === id }

  function isAccessDenied(reason: unknown) {
    return reason instanceof ApiClientError && [401, 403, 404].includes(reason.status)
  }

  function denyAccess(message = '当前导出或制品不可读取，旧操作已停用。') {
    const currentId = exportId.value
    if (currentId) recoveredExportId.value = currentId
    accepted.value = null; exportView.value = null; job.value = null; artifact.value = null
    accessDenied.value = true; error.value = message; artifactError.value = message; state.value = 'error'
  }

  function clear() {
    accepted.value = null; exportView.value = null; job.value = null; artifact.value = null
    const currentProject = projectId.value
    projectId.value = null; recoveredExportId.value = null; pendingSubmission.value = null; error.value = null; artifactError.value = null; state.value = 'idle'
    accessDenied.value = false
    if (currentProject) saveExport(currentProject, null)
  }

  async function refresh() {
    const currentProject = projectId.value
    const currentId = exportId.value
    if (!currentProject || !currentId) return
    state.value = 'loading'; error.value = null
    try {
      const currentExport = await apiClient.get<ContractExportView>(`/exports/${encodeURIComponent(currentId)}`)
      if (!isCurrent(currentProject, currentId)) return
      if (!isExportView(currentExport) || currentExport.id !== currentId) throw new Error('Export identity mismatch')
      exportView.value = currentExport
      const currentJob = await apiClient.get<ContractJob>(`/jobs/${encodeURIComponent(currentExport.job_id)}`)
      if (!isCurrent(currentProject, currentId)) return
      if (currentJob.id !== currentExport.job_id || currentJob.project_id !== projectId.value) throw new Error('Job identity mismatch')
      job.value = currentJob
      artifact.value = null; artifactError.value = null
      if (currentExport.status === 'SUCCEEDED' && currentExport.artifact_id) {
        try {
          const metadata = await useArtifactStore().getArtifact(currentExport.artifact_id)
          if (!isCurrent(currentProject, currentId)) return
          if (metadata.id !== currentExport.artifact_id || metadata.project_id !== projectId.value || metadata.kind !== 'export_report') throw new Error('Export artifact mismatch')
          artifact.value = metadata
        } catch (reason) {
          if (isAccessDenied(reason)) throw reason
          artifactError.value = '导出已完成，但制品元数据暂不可用。'
        }
      }
      if (!isCurrent(currentProject, currentId)) return
      accessDenied.value = false
      state.value = 'ready'
    } catch (reason) {
      if (!isCurrent(currentProject, currentId)) return
      if (isAccessDenied(reason)) denyAccess()
      else error.value = '导出状态暂不可用，请刷新重试。'
      state.value = 'error'
    }
  }

  async function restore(forProjectId: string) {
    const saved = readSavedExport(forProjectId)
    if (projectId.value !== forProjectId) {
      accepted.value = null; exportView.value = null; job.value = null; artifact.value = null
      recoveredExportId.value = null; pendingSubmission.value = null; error.value = null; artifactError.value = null; state.value = 'idle'; accessDenied.value = false
    }
    projectId.value = forProjectId
    if (saved?.pending) {
      pendingSubmission.value = saved.pending
      error.value = '上次提交尚未确认是否受理；相同选择将复用原幂等键安全重试。'
      state.value = 'error'
      return
    }
    pendingSubmission.value = null
    if (!saved?.exportId) return
    if (exportId.value === saved.exportId) { await refresh(); return }
    accepted.value = null; exportView.value = null; job.value = null; artifact.value = null
    recoveredExportId.value = saved.exportId
    await refresh()
  }

  async function create(body: ContractCreateRecordExportBody) {
    if (busy.value || accessDenied.value) return
    if (!isExportCreateBody(body)) { error.value = '导出请求字段不符合当前合同。'; state.value = 'error'; return }
    const fingerprint = JSON.stringify(body)
    const saved = readSavedExport(body.project_id)
    const pending = pendingSubmission.value ?? saved?.pending ?? null
    if (pending && pending.fingerprint !== fingerprint) {
      error.value = '上次导出提交状态未知；请恢复并重试同一选择后，再创建不同内容的导出。'
      state.value = 'error'
      return
    }
    const pendingRequest = pending ?? { body, fingerprint, idempotencyKey: createIdempotencyKey() }
    pendingSubmission.value = pendingRequest
    saveExport(body.project_id, { pending: pendingRequest })
    busy.value = true; state.value = 'submitting'; error.value = null; artifactError.value = null
    projectId.value = body.project_id; recoveredExportId.value = null; accepted.value = null; exportView.value = null; job.value = null; artifact.value = null
    try {
      const result = await apiClient.requestWithMeta<ContractExportAccepted>('/exports', { method: 'POST', body: pendingRequest.body, idempotencyKey: pendingRequest.idempotencyKey })
      if (result.response.status !== 202 || !isExportAccepted(result.data) || !result.location) throw new Error('Invalid export acceptance')
      if (!isExpectedExportLocation(result.location, result.data.id)) throw new Error('Invalid export Location')
      saveExport(body.project_id, { exportId: result.data.id })
      if (projectId.value === body.project_id) {
        accepted.value = result.data
        pendingSubmission.value = null
        accessDenied.value = false
        await refresh()
      }
    } catch (reason) {
      if (projectId.value === body.project_id) {
        if (isAccessDenied(reason)) {
          pendingSubmission.value = null
          saveExport(body.project_id, null)
          denyAccess('当前项目不允许读取或创建导出；请确认项目访问权限后重试。')
        } else if (!accepted.value) error.value = '导出请求的受理状态未知；请用相同选择安全重试，避免创建不同请求。'
        else error.value = '导出已受理，但状态读取暂不可用。'
        state.value = 'error'
      }
    } finally { busy.value = false }
  }

  async function cancel() {
    if (busy.value || accessDenied.value || state.value !== 'ready' || !canCancel.value || !job.value?.etag) return
    const currentProject = projectId.value; const currentExport = exportId.value; const currentJob = job.value
    if (!currentProject || !currentExport) return
    busy.value = true; error.value = null
    try {
      const body: ContractCancelJobBody = { reason: null }
      const updatedJob = await apiClient.post<ContractJob>(`/jobs/${encodeURIComponent(currentJob.id)}/cancel`, body, { idempotencyKey: createIdempotencyKey(), ifMatch: currentJob.etag })
      if (!isCurrent(currentProject, currentExport)) return
      job.value = updatedJob
      await refresh()
    } catch (reason) {
      if (isCurrent(currentProject, currentExport)) {
        if (isAccessDenied(reason)) denyAccess('当前任务不再可读取或操作；旧操作已停用。')
        else { error.value = '取消请求失败；请刷新任务状态后重试。'; state.value = 'error' }
      }
    }
    finally { busy.value = false }
  }

  async function retry(confirmationId: string) {
    if (busy.value || accessDenied.value || state.value !== 'ready' || !canRetry.value || !job.value?.etag || !confirmationId) return
    const currentProject = projectId.value; const currentExport = exportId.value; const currentJob = job.value
    if (!currentProject || !currentExport) return
    busy.value = true; error.value = null
    try {
      const body: ContractRetryJobBody = { confirmation_id: confirmationId }
      const updatedJob = await apiClient.post<ContractJob>(`/jobs/${encodeURIComponent(currentJob.id)}/retry`, body, { idempotencyKey: createIdempotencyKey(), ifMatch: currentJob.etag })
      if (!isCurrent(currentProject, currentExport)) return
      job.value = updatedJob
      await refresh()
    } catch (reason) {
      if (isCurrent(currentProject, currentExport)) {
        if (isAccessDenied(reason)) denyAccess('当前任务不再可读取或操作；旧操作已停用。')
        else { error.value = '重试请求失败；请刷新任务与确认状态后重试。'; state.value = 'error' }
      }
    }
    finally { busy.value = false }
  }

  function invalidateAccess(message = '当前制品不可读取或已失去下载权限；旧操作已停用。', expectedProject?: string | null, expectedId?: string | null) {
    if (expectedProject !== undefined && expectedId !== undefined && (!expectedProject || !expectedId || !isCurrent(expectedProject, expectedId))) return
    denyAccess(message)
  }

  return { accepted, exportView, job, artifact, projectId, pendingSubmission, state, error, artifactError, accessDenied, busy, exportId, isPending, canCancel, canRetry, clear, refresh, restore, create, cancel, retry, invalidateAccess }
})
