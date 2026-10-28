import { apiClient, createIdempotencyKey, type ApiResult } from '@/core/api/client'
import type {
  BatchAccepted,
  BatchCancelBody,
  BatchChromatographyType,
  BatchDraft,
  BatchDraftCreate,
  BatchDraftPatch,
  BatchExportAccepted,
  BatchExportCreate,
  BatchExportView,
  BatchRetryFailedBody,
  BatchRetryPlan,
  BatchRetryPlanBody,
  BatchRowsPage,
  BatchSubmitBody,
  BatchTemplateFormat,
  BatchToolCapability,
  BatchTypeCapability,
  BatchValidateBody,
  BatchValidation,
  BatchValidationAccepted,
  BatchValidationRowsPage,
} from './batch-types'

export interface BatchClientScope {
  actorId: string
  projectId: string
  type: BatchChromatographyType
  tool: 'batch'
}

export interface BatchTemplate {
  blob: Blob
  version: string
  sha256: string
}

function stableSerialize(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableSerialize).join(',')}]`
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${stableSerialize(record[key])}`).join(',')}}`
  }
  return JSON.stringify(value) ?? 'null'
}

function queryString(values: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) if (value !== undefined) query.set(key, String(value))
  return query.size ? `?${query}` : ''
}

export function createBatchClient(scope: BatchClientScope) {
  const scopeKey = stableSerialize([scope.actorId, scope.projectId, scope.type, scope.tool])
  const keys = new Map<string, string>()
  const inFlight = new Map<string, Promise<unknown>>()

  function idempotent<T>(operation: string, requestVersion: unknown, request: (key: string) => Promise<T>): Promise<T> {
    const signature = stableSerialize([scopeKey, operation, requestVersion])
    const pending = inFlight.get(signature)
    if (pending) return pending as Promise<T>
    let key = keys.get(signature)
    if (!key) {
      key = createIdempotencyKey()
      keys.set(signature, key)
    }
    const result = request(key)
    inFlight.set(signature, result)
    void result.then(
      () => { if (inFlight.get(signature) === result) inFlight.delete(signature) },
      () => { if (inFlight.get(signature) === result) inFlight.delete(signature) },
    )
    return result
  }

  function batchPath(id: string, suffix = ''): string {
    return `/batch-predictions/${encodeURIComponent(id)}${suffix}`
  }

  function narrowToolCapability(value: BatchToolCapability): BatchToolCapability {
    if (!value || typeof value !== 'object') throw new Error('服务端工具能力格式无效。')
    if ('chromatography_type' in value) {
      if (value.id !== 'batch' || value.chromatography_type !== scope.type || !Array.isArray(value.templates)
        || !value.model_constraints || !Array.isArray(value.model_constraints.compatible_models) || !Array.isArray(value.model_constraints.template_versions)) {
        throw new Error('服务端批量工具能力与当前色谱类型不匹配。')
      }
      if (scope.type === 'tlc') {
        if (value.chromatography_type !== 'tlc') throw new Error('TLC 批量能力分支无效。')
        return value
      }
      if (value.chromatography_type === 'tlc') throw new Error('非 TLC 批量能力分支无效。')
      return value
    }
    if (typeof value.id !== 'string' || !value.id || value.id === 'batch' || 'templates' in value) throw new Error('批量工具响应缺少色谱类型标识。')
    return value
  }

  return {
    async getTypeCapability(): Promise<BatchTypeCapability> {
      const capability = await apiClient.get<BatchTypeCapability>(`/prediction/types/${encodeURIComponent(scope.type)}`)
      if (!capability || capability.id !== scope.type) throw new Error('服务端返回的色谱类型与当前工作区不匹配。')
      return capability
    },
    async getToolCapability(schemaVersion: string): Promise<BatchToolCapability> {
      const capability = await apiClient.get<BatchToolCapability>(`/prediction/types/${encodeURIComponent(scope.type)}/tools/${encodeURIComponent(scope.tool)}/schema${queryString({ schema_version: schemaVersion })}`)
      return narrowToolCapability(capability)
    },
    async downloadTemplate(modelId: string, format: BatchTemplateFormat, templateVersion?: string): Promise<BatchTemplate> {
      const result = await apiClient.requestWithMeta<Blob>(`/prediction/types/${encodeURIComponent(scope.type)}/batch-template${queryString({ tool: scope.tool, model_id: modelId, format, template_version: templateVersion })}`)
      const version = result.response.headers.get('X-Template-Version')
      const sha256 = result.response.headers.get('X-Template-SHA256')
      if (!version || !sha256 || !/^[0-9a-f]{64}$/.test(sha256)) throw new Error('批量模板响应缺少有效版本或 SHA-256。')
      if (templateVersion && version !== templateVersion) throw new Error('服务端返回的批量模板版本与请求版本不一致。')
      return { blob: result.data, version, sha256 }
    },
    createDraft(body: BatchDraftCreate): Promise<ApiResult<BatchDraft>> {
      return idempotent('draft:create', body, (key) => apiClient.requestWithMeta<BatchDraft>('/batch-predictions/drafts', { method: 'POST', body, idempotencyKey: key }))
    },
    getBatch(id: string): Promise<ApiResult<BatchDraft>> {
      return apiClient.requestWithMeta<BatchDraft>(batchPath(id))
    },
    patchDraft(id: string, body: BatchDraftPatch, etag: string): Promise<ApiResult<BatchDraft>> {
      if (!etag) return Promise.reject(new Error('更新批量草稿需要服务端 ETag。'))
      return idempotent(`draft:patch:${id}`, [etag, body], (key) => apiClient.requestWithMeta<BatchDraft>(batchPath(id), { method: 'PATCH', body, ifMatch: etag, idempotencyKey: key }))
    },
    validate(id: string, etag: string): Promise<ApiResult<BatchValidationAccepted>> {
      if (!etag) return Promise.reject(new Error('校验批量草稿需要服务端 ETag。'))
      const body: BatchValidateBody = {}
      return idempotent(`draft:validate:${id}`, [etag, body], (key) => apiClient.requestWithMeta<BatchValidationAccepted>(batchPath(id, '/validate'), { method: 'POST', body, ifMatch: etag, idempotencyKey: key }))
    },
    getValidation(id: string): Promise<BatchValidation> {
      return apiClient.get<BatchValidation>(batchPath(id, '/validation'))
    },
    listValidationRows(id: string, params: { status?: string; cursor?: string; limit?: number } = {}): Promise<BatchValidationRowsPage> {
      return apiClient.get<BatchValidationRowsPage>(batchPath(id, `/validation/rows${queryString(params)}`))
    },
    submit(id: string, confirmationId: string): Promise<ApiResult<BatchAccepted>> {
      if (!confirmationId) return Promise.reject(new Error('提交批量任务需要服务端确认记录。'))
      const body: BatchSubmitBody = { confirmation_id: confirmationId }
      return idempotent(`draft:submit:${id}`, body, (key) => apiClient.requestWithMeta<BatchAccepted>(batchPath(id, '/submit'), { method: 'POST', body, idempotencyKey: key }))
    },
    listRows(id: string, params: { status?: string; cursor?: string; limit?: number } = {}): Promise<BatchRowsPage> {
      return apiClient.get<BatchRowsPage>(batchPath(id, `/rows${queryString(params)}`))
    },
    cancel(id: string, etag: string, reason?: string): Promise<ApiResult<BatchDraft>> {
      if (!etag) return Promise.reject(new Error('取消批量任务需要服务端 ETag。'))
      const body: BatchCancelBody = reason === undefined ? {} : { reason }
      return idempotent(`draft:cancel:${id}`, [etag, body], (key) => apiClient.requestWithMeta<BatchDraft>(batchPath(id, '/cancel'), { method: 'POST', body, ifMatch: etag, idempotencyKey: key }))
    },
    createRetryPlan(id: string, rowIds: string[], etag: string): Promise<ApiResult<BatchRetryPlan>> {
      if (!etag) return Promise.reject(new Error('创建失败行重试计划需要服务端 ETag。'))
      const body: BatchRetryPlanBody = { row_ids: rowIds }
      return idempotent(`retry-plan:create:${id}`, [etag, body], (key) => apiClient.requestWithMeta<BatchRetryPlan>(batchPath(id, '/retry-plans'), { method: 'POST', body, ifMatch: etag, idempotencyKey: key }))
    },
    retryFailed(id: string, retryPlanId: string, confirmationId: string, etag: string): Promise<ApiResult<BatchAccepted>> {
      if (!etag) return Promise.reject(new Error('重试失败行需要服务端 ETag。'))
      if (!retryPlanId || !confirmationId) return Promise.reject(new Error('重试失败行需要重试计划和确认记录。'))
      const body: BatchRetryFailedBody = { retry_plan_id: retryPlanId, confirmation_id: confirmationId }
      return idempotent(`retry-failed:${id}`, [etag, body], (key) => apiClient.requestWithMeta<BatchAccepted>(batchPath(id, '/retry-failed'), { method: 'POST', body, ifMatch: etag, idempotencyKey: key }))
    },
    createExport(id: string, body: BatchExportCreate, etag: string): Promise<ApiResult<BatchExportAccepted>> {
      if (!etag) return Promise.reject(new Error('创建批量导出需要服务端 ETag。'))
      return idempotent(`export:create:${id}`, [etag, body], (key) => apiClient.requestWithMeta<BatchExportAccepted>(batchPath(id, '/exports'), { method: 'POST', body, ifMatch: etag, idempotencyKey: key }))
    },
    getExport(exportId: string): Promise<BatchExportView> {
      return apiClient.get<BatchExportView>(`/batch-predictions/exports/${encodeURIComponent(exportId)}`)
    },
    downloadExport(exportId: string): Promise<Blob> {
      return apiClient.request<Blob>(`/batch-predictions/exports/${encodeURIComponent(exportId)}/download`, { headers: { Accept: 'application/octet-stream' } })
    },
  }
}
