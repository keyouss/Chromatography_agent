import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, createIdempotencyKey, type ApiResult } from '@/core/api/client'
import type {
  ContractArchiveDataVersionBody,
  ContractAsyncAccepted,
  ContractDataDomain,
  ContractDataDomainListEnvelope,
  ContractDataImport,
  ContractDataImportCreateBody,
  ContractDataImportPatchBody,
  ContractDataImportValidation,
  ContractDataVersion,
  ContractDataVersionListEnvelope,
  ContractJob,
  ContractEmptyPayload,
  ContractDataImportCommitBody,
} from '@/core/contract-types'
import type { Artifact } from '@/core/types'

const VALIDATION_REPORT_MAX_BYTES = 2 * 1024 * 1024
const REPORT_ISSUE_LIMIT = 500
const SHA256 = /^[0-9a-f]{64}$/

export type DataImportValidationReportIssue = {
  source_row_number: number
  measurement_id: string | null
  field: string | null
  raw_value_preview: string
  raw_value_sha256: string | null
  raw_value_truncated: boolean
  code: string
  message: string
}

export type DataImportValidationReport = {
  report_schema_version: 'data-import-validation-report-1'
  status: 'VALID' | 'INVALID' | 'UNAVAILABLE' | 'UNSUPPORTED_IMPORT_MODE'
  project_id: string
  import_id: string
  revision: number
  config_hash: string
  domain_id: string
  domain_schema_version: string
  source_artifact_id: string
  source_sha256: string
  encoding: string | null
  parser_revision: string
  row_count: number
  counts: ContractDataImportValidation['counts']
  issues: DataImportValidationReportIssue[]
  total_issue_count: number
  issues_truncated: boolean
}

export type BoundValidationReport = { report: DataImportValidationReport; historical: boolean }

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function hasKeys(value: Record<string, unknown>, keys: string[]) {
  const actual = Object.keys(value).sort()
  return actual.length === keys.length && actual.every((key, index) => key === [...keys].sort()[index])
}

function boundedText(value: unknown, max: number, allowEmpty = false): value is string {
  return typeof value === 'string' && Array.from(value).length <= max && (allowEmpty || value.length > 0)
}

export function parseDataImportValidationReport(value: unknown): DataImportValidationReport {
  const fields = ['report_schema_version', 'status', 'project_id', 'import_id', 'revision', 'config_hash', 'domain_id', 'domain_schema_version', 'source_artifact_id', 'source_sha256', 'encoding', 'parser_revision', 'row_count', 'counts', 'issues', 'total_issue_count', 'issues_truncated']
  const countFields = ['new', 'updated', 'unchanged', 'invalid', 'duplicate']
  const issueFields = ['source_row_number', 'measurement_id', 'field', 'raw_value_preview', 'raw_value_sha256', 'raw_value_truncated', 'code', 'message']
  if (!isObject(value) || !hasKeys(value, fields)) throw new Error('验证报告 schema 不受支持或字段不完整。')
  if (value.report_schema_version !== 'data-import-validation-report-1') throw new Error('验证报告版本不受支持。')
  if (!['VALID', 'INVALID', 'UNAVAILABLE', 'UNSUPPORTED_IMPORT_MODE'].includes(String(value.status))) throw new Error('验证报告状态无效。')
  if (![value.project_id, value.import_id, value.domain_id, value.domain_schema_version, value.source_artifact_id, value.parser_revision].every((item) => boundedText(item, 128))) throw new Error('验证报告绑定字段无效。')
  if (!Number.isSafeInteger(value.revision) || Number(value.revision) < 1 || !Number.isSafeInteger(value.row_count) || Number(value.row_count) < 0 || Number(value.row_count) > 10000) throw new Error('验证报告行数或 revision 无效。')
  if (!boundedText(value.config_hash, 64) || !SHA256.test(value.config_hash) || !boundedText(value.source_sha256, 64) || !SHA256.test(value.source_sha256)) throw new Error('验证报告 SHA-256 无效。')
  if (value.encoding !== null && !boundedText(value.encoding, 64)) throw new Error('验证报告编码字段无效。')
  if (!isObject(value.counts) || !hasKeys(value.counts, countFields)) throw new Error('验证报告计数无效。')
  const counts = value.counts
  if (countFields.some((key) => typeof counts[key] !== 'number' || !Number.isSafeInteger(counts[key]) || Number(counts[key]) < 0)) throw new Error('验证报告计数无效。')
  if (!Array.isArray(value.issues) || value.issues.length > REPORT_ISSUE_LIMIT || value.issues.some((issue) => {
    if (!isObject(issue) || !hasKeys(issue, issueFields)) return true
    return !Number.isSafeInteger(issue.source_row_number) || Number(issue.source_row_number) < 1
      || (issue.measurement_id !== null && !boundedText(issue.measurement_id, 128))
      || (issue.field !== null && !boundedText(issue.field, 128))
      || !boundedText(issue.raw_value_preview, 256, true)
      || (issue.raw_value_sha256 !== null && (!boundedText(issue.raw_value_sha256, 64) || !SHA256.test(issue.raw_value_sha256)))
      || typeof issue.raw_value_truncated !== 'boolean'
      || !boundedText(issue.code, 64) || !boundedText(issue.message, 256)
      || (issue.raw_value_truncated && issue.raw_value_sha256 === null)
  })) throw new Error('验证报告问题列表无效。')
  if (!Number.isSafeInteger(value.total_issue_count) || Number(value.total_issue_count) < value.issues.length || typeof value.issues_truncated !== 'boolean'
    || value.issues_truncated !== (Number(value.total_issue_count) > value.issues.length)) throw new Error('验证报告截断标记与问题总数不一致。')
  return value as DataImportValidationReport
}

export function bindDataImportValidationReport(
  report: DataImportValidationReport,
  validation: ContractDataImportValidation,
  dataImport: ContractDataImport,
  reportArtifact: Artifact,
  sourceArtifact: Artifact,
): BoundValidationReport {
  if (validation.import_id !== dataImport.id || !validation.report_artifact_id || validation.report_artifact_id !== reportArtifact.id
    || validation.revision !== report.revision || validation.config_hash !== report.config_hash
    || validation.parser_revision !== report.parser_revision || !validation.parser_hash
    || report.project_id !== dataImport.project_id || report.import_id !== dataImport.id || report.domain_id !== dataImport.domain_id
    || reportArtifact.project_id !== dataImport.project_id || reportArtifact.kind !== 'export_report' || reportArtifact.media_type !== 'application/json'
    || reportArtifact.status !== 'AVAILABLE' || reportArtifact.download_allowed !== true || reportArtifact.size_bytes <= 0 || reportArtifact.size_bytes > VALIDATION_REPORT_MAX_BYTES
    || !reportArtifact.sha256 || !SHA256.test(reportArtifact.sha256)
    || reportArtifact.source_ref?.type !== 'data_import_validation' || reportArtifact.source_ref.id !== validation.id
    || sourceArtifact.id !== report.source_artifact_id || sourceArtifact.project_id !== dataImport.project_id || sourceArtifact.kind !== 'dataset_package'
    || sourceArtifact.status !== 'AVAILABLE' || sourceArtifact.scan_status !== 'PASSED' || sourceArtifact.size_bytes <= 0
    || sourceArtifact.sha256 !== report.source_sha256
    || Object.keys(validation.counts).some((key) => validation.counts[key as keyof typeof validation.counts] !== report.counts[key as keyof typeof report.counts])) {
    throw new Error('验证报告与所属 validation、项目、源文件或服务端制品元数据不匹配。')
  }
  const statusMatches = validation.status === 'VALID' ? report.status === 'VALID'
    : validation.status === 'INVALID' ? ['INVALID', 'UNAVAILABLE', 'UNSUPPORTED_IMPORT_MODE'].includes(report.status)
      : validation.status === 'STALE'
  if (!statusMatches) throw new Error('验证报告状态与所属 validation 不匹配。')
  const sameRevision = report.revision === dataImport.revision && report.config_hash === dataImport.config_hash
  if (sameRevision && (report.source_artifact_id !== dataImport.artifact_id || report.domain_schema_version !== dataImport.schema_version)) {
    throw new Error('当前 revision 的验证报告与导入输入不匹配。')
  }
  const historical = !sameRevision || validation.status === 'STALE' || validation.id !== dataImport.validation_ref
  return { report, historical }
}

export const useDatasetStore = defineStore('datasets', () => {
  const domains = ref<ContractDataDomain[]>([])
  const imports = ref<Record<string, ContractDataImport>>({})
  const validations = ref<Record<string, ContractDataImportValidation>>({})
  const versions = ref<ContractDataVersion[]>([])
  const domainPage = ref<ContractDataDomainListEnvelope['page'] | null>(null)
  const versionPage = ref<ContractDataVersionListEnvelope['page'] | null>(null)
  const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const error = ref<string | null>(null)

  async function listDomains(cursor?: string) {
    state.value = 'loading'; error.value = null
    try {
      const result = await apiClient.requestWithMeta<ContractDataDomain[]>(`/data-domains${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`)
      domains.value = result.data; domainPage.value = result.page ?? null; state.value = 'ready'; return result
    } catch (reason) { state.value = 'error'; error.value = reason instanceof Error ? reason.message : '数据域暂不可用'; throw reason }
  }

  async function getDomainSchema(domainId: string, schemaVersion?: string) {
    const query = schemaVersion ? `?schema_version=${encodeURIComponent(schemaVersion)}` : ''
    return apiClient.get<ContractDataDomain>(`/data-domains/${encodeURIComponent(domainId)}/schema${query}`)
  }

  async function createImport(body: ContractDataImportCreateBody, isCurrent: () => boolean = () => true) {
    const result = await apiClient.requestWithMeta<ContractDataImport>('/data-imports', { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
    if (isCurrent() && result.data.project_id === body.project_id) imports.value[result.data.id] = result.data
    return result
  }

  async function getImport(importId: string, isCurrent: () => boolean = () => true, projectId?: string) {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    const result = await apiClient.get<ContractDataImport>(`/data-imports/${encodeURIComponent(importId)}${query}`)
    if (result.id !== importId || (projectId && result.project_id !== projectId)) throw new Error('服务端导入记录与当前项目不匹配。')
    if (isCurrent()) imports.value[result.id] = result
    return result
  }

  async function patchImport(importId: string, body: ContractDataImportPatchBody, ifMatch: string, projectId?: string, isCurrent: () => boolean = () => true) {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    const result = await apiClient.requestWithMeta<ContractDataImport>(`/data-imports/${encodeURIComponent(importId)}${query}`, { method: 'PATCH', body, ifMatch, idempotencyKey: createIdempotencyKey() })
    if (isCurrent() && (!projectId || result.data.project_id === projectId)) imports.value[result.data.id] = result.data
    return result
  }

  async function validateImport(importId: string, ifMatch: string, projectId?: string): Promise<ApiResult<ContractAsyncAccepted>> {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    const body: ContractEmptyPayload = {}
    return apiClient.requestWithMeta<ContractAsyncAccepted>(`/data-imports/${encodeURIComponent(importId)}/validate${query}`, { method: 'POST', body, ifMatch, idempotencyKey: createIdempotencyKey() })
  }

  async function getValidation(importId: string, isCurrent: () => boolean = () => true, projectId?: string, expected?: { id?: string; revision?: number; configHash?: string; allowStale?: boolean }) {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    const result = await apiClient.get<ContractDataImportValidation>(`/data-imports/${encodeURIComponent(importId)}/validation${query}`)
    if (result.import_id !== importId || (expected?.id && result.id !== expected.id)
      || (result.status !== 'STALE' || !expected?.allowStale) && ((expected?.revision !== undefined && result.revision !== expected.revision)
      || (expected?.configHash && result.config_hash !== expected.configHash))) throw new Error('服务端验证结果与当前导入记录不匹配。')
    if (isCurrent()) validations.value[importId] = result
    return result
  }

  async function getBoundJob(jobId: string, projectId: string, importId: string, kind: 'data_import_validation' | 'data_import_commit') {
    const job = await apiClient.get<ContractJob>(`/jobs/${encodeURIComponent(jobId)}`)
    if (job.id !== jobId || job.project_id !== projectId || job.kind !== kind || job.resource_ref.type !== 'data_import' || job.resource_ref.id !== importId || !Number.isInteger(job.attempt) || job.attempt < 1) {
      throw new Error('服务端任务与当前项目、导入记录或任务类型不匹配。')
    }
    return job
  }

  async function getValidationArtifact(artifactId: string) {
    return apiClient.get<Artifact>(`/artifacts/${encodeURIComponent(artifactId)}`)
  }

  async function getValidationReport(artifactId: string, signal?: AbortSignal) {
    return apiClient.getBoundedRawJson(`/artifacts/${encodeURIComponent(artifactId)}/download`, VALIDATION_REPORT_MAX_BYTES, { signal })
  }

  async function commitImport(importId: string, confirmationId: string, projectId?: string): Promise<ApiResult<ContractAsyncAccepted>> {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    const body: ContractDataImportCommitBody = { confirmation_id: confirmationId }
    return apiClient.requestWithMeta<ContractAsyncAccepted>(`/data-imports/${encodeURIComponent(importId)}/commit${query}`, { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
  }

  async function listVersions(projectId: string, domainId: string, cursor?: string, isCurrent: () => boolean = () => true) {
    const params = new URLSearchParams({ project_id: projectId, domain_id: domainId })
    if (cursor) params.set('cursor', cursor)
    const result = await apiClient.requestWithMeta<ContractDataVersion[]>(`/data-versions?${params.toString()}`)
    if (result.data.some((version) => version.project_id !== projectId || version.domain_id !== domainId)) throw new Error('服务端版本列表包含其他项目或数据域的记录。')
    if (isCurrent()) { versions.value = result.data; versionPage.value = result.page ?? null }
    return result
  }

  async function getVersion(versionId: string) { return apiClient.get<ContractDataVersion>(`/data-versions/${encodeURIComponent(versionId)}`) }

  async function archiveVersion(versionId: string, confirmationId: string, projectId?: string) {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    const body: ContractArchiveDataVersionBody = { confirmation_id: confirmationId }
    return apiClient.requestWithMeta<ContractDataVersion>(`/data-versions/${encodeURIComponent(versionId)}/archive${query}`, { method: 'POST', body, idempotencyKey: createIdempotencyKey() })
  }

  return { domains, imports, validations, versions, domainPage, versionPage, state, error, listDomains, getDomainSchema, createImport, getImport, patchImport, validateImport, getValidation, getBoundJob, getValidationArtifact, getValidationReport, commitImport, listVersions, getVersion, archiveVersion }
})
