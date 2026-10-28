<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ConfirmationCard from '@/components/ConfirmationCard.vue'
import type { ContractArtifactAccepted, ContractAsyncAccepted, ContractCreateUploadBody, ContractDataDomain, ContractDataImport, ContractDataImportCreateBody, ContractDataImportValidation, ContractDataVersion, ContractJob } from '@/core/contract-types'
import type { Artifact } from '@/core/types'
import { projectSelectionState, resolveProjectId } from '@/core/project-context'
import { useAppStore } from '@/stores/app'
import { useArtifactStore } from '@/stores/artifacts'
import type { ResourceRef } from '@/core/types'
import { ApiClientError } from '@/core/api/client'
import { useConfirmationStore } from '@/stores/confirmations'
import { statusLabel } from '@/core/status-labels'
import { useDatasetStore } from './store'
import { bindDataImportValidationReport, parseDataImportValidationReport, type BoundValidationReport } from './store'

const store = useDatasetStore()
const app = useAppStore()
const artifacts = useArtifactStore()
const confirmations = useConfirmationStore()
const selectedProjectId = ref('')
const domainId = ref('')
const schema = ref<ContractDataDomain | null>(null)
const schemaState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const schemaError = ref('')
const file = ref<File | null>(null)
const acceptedUpload = ref<ContractArtifactAccepted | null>(null)
const uploadedArtifact = ref<Artifact | null>(null)
const uploadState = ref<'idle' | 'uploading' | 'checking' | 'ready' | 'pending' | 'error'>('idle')
const uploadMessage = ref('')
const importMode = ref<'append' | 'upsert'>('append')
const sourceDescription = ref('')
const mappingJson = ref('{}')
const selectedImportId = ref<string | null>(null)
const formError = ref<string | null>(null)
const actionBusy = ref(false)
const jobOperation = ref<'validate' | 'commit' | null>(null)
const jobState = ref<'idle' | 'checking' | 'polling' | 'refreshing' | 'terminal' | 'error'>('idle')
const jobId = ref('')
const job = ref<ContractJob | null>(null)
const jobError = ref('')
const validation = ref<ContractDataImportValidation | null>(null)
const validationState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const validationError = ref('')
const report = ref<BoundValidationReport | null>(null)
const reportArtifact = ref<Artifact | null>(null)
const reportState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const reportError = ref('')
const importRefreshRequired = ref(false)
const restoreError = ref('')
const versionLoading = ref(false)
const versionError = ref('')
const selectedVersionId = ref('')
const versionDetail = ref<ContractDataVersion | null>(null)
const detailState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
let projectGeneration = 0
let schemaGeneration = 0
let uploadGeneration = 0
let versionGeneration = 0
let jobGeneration = 0
let reportGeneration = 0
let jobAttempt: number | null = null
let jobTimer: number | null = null
let reportController: AbortController | null = null
let restoringImportPointer = false
let restoredActorId = ''
const IMPORT_POINTER_KEY = 'chrom-platform.datasets.import-pointer'

type ImportPointer = { actorId: string; projectId: string; domainId: string; importId: string }

function readImportPointer(): ImportPointer | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(IMPORT_POINTER_KEY) ?? 'null')
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null
    const record = value as Record<string, unknown>
    if (Object.keys(record).sort().join('|') !== 'actorId|domainId|importId|projectId'
      || ![record.actorId, record.projectId, record.domainId, record.importId].every((part) => typeof part === 'string' && part.length > 0 && part.length <= 128)) return null
    return record as ImportPointer
  } catch { return null }
}

function persistImportPointer(importId: string) {
  const actorId = app.accessContext?.actor_id
  if (!actorId || !projectId.value || !domainId.value) return
  try { localStorage.setItem(IMPORT_POINTER_KEY, JSON.stringify({ actorId, projectId: projectId.value, domainId: domainId.value, importId })) } catch { /* browser storage can be unavailable */ }
}

function clearImportPointer() {
  try { localStorage.removeItem(IMPORT_POINTER_KEY) } catch { /* browser storage can be unavailable */ }
}

const availableProjects = computed(() => app.accessContext?.accessible_projects ?? [])
const projectId = computed(() => resolveProjectId(app.accessContext, selectedProjectId.value) ?? '')
const projectState = computed(() => projectSelectionState(app.accessContext, selectedProjectId.value))
const selectedDomain = computed(() => store.domains.find((domain) => domain.id === domainId.value) ?? null)
const schemaFields = computed(() => Object.entries(schema.value?.record_schema ?? {}))
const selectedImport = computed(() => selectedImportId.value ? store.imports[selectedImportId.value] ?? null : null)
const importRef = computed<ResourceRef | null>(() => selectedImport.value ? { id: selectedImport.value.id, type: 'data_import' } : null)
const confirmationMatches = computed(() => {
  const currentImport = selectedImport.value
  const preview = confirmations.preview
  const confirmation = confirmations.confirmation
  if (!currentImport || !importRef.value || !preview || !confirmation || !confirmations.isFor(importRef.value, 'data_import.commit')) return false
  return preview.revision === currentImport.revision
    && preview.etag === currentImport.etag
    && preview.config_hash.trim().length > 0
    && confirmation.revision === currentImport.revision
    && confirmation.config_hash === preview.config_hash
})
const validationMatchesImport = computed(() => Boolean(selectedImport.value && validation.value
  && validation.value.import_id === selectedImport.value.id
  && validation.value.id === selectedImport.value.validation_ref
  && validation.value.revision === selectedImport.value.revision
  && validation.value.config_hash === selectedImport.value.config_hash))
const canConfirmCommit = computed(() => Boolean(selectedImport.value?.status === 'READY'
  && validation.value?.status === 'VALID' && validationMatchesImport.value
  && jobState.value !== 'checking' && jobState.value !== 'polling' && jobState.value !== 'refreshing'
  && !actionBusy.value && !importRefreshRequired.value))
const jobBusy = computed(() => jobState.value === 'checking' || jobState.value === 'polling' || jobState.value === 'refreshing')
const artifactReady = computed(() => Boolean(uploadedArtifact.value
  && uploadedArtifact.value.project_id === projectId.value
  && uploadedArtifact.value.kind === 'dataset_package'
  && uploadedArtifact.value.status === 'AVAILABLE'
  && uploadedArtifact.value.scan_status === 'PASSED'))
const artifactId = computed(() => artifactReady.value ? uploadedArtifact.value!.id : '')
const domainNotReadyReason = computed(() => {
  if (!schema.value) return ''
  if (!schemaFields.value.length) return '服务端 schema 没有可映射字段。'
  if (!schema.value.identity_fields.length) return '服务端 schema 未定义记录身份字段。'
  if (!schema.value.supported_import_modes.length) return '服务端未声明支持的导入模式。'
  return ''
})
const schemaVersions = computed(() => selectedDomain.value ? [selectedDomain.value.version] : [])
const canCreateImport = computed(() => Boolean(projectId.value && schema.value && !domainNotReadyReason.value && artifactId.value
  && schema.value.supported_import_modes.includes(importMode.value) && !actionBusy.value))

function stopJobPolling() {
  jobGeneration += 1
  if (jobTimer !== null) window.clearTimeout(jobTimer)
  jobTimer = null
}

function resetValidationReport() {
  reportGeneration += 1
  reportController?.abort(); reportController = null
  report.value = null; reportArtifact.value = null; reportState.value = 'idle'; reportError.value = ''
}

function resetJobContext() {
  stopJobPolling(); resetValidationReport(); jobAttempt = null
  jobOperation.value = null; jobState.value = 'idle'; jobId.value = ''; job.value = null; jobError.value = ''
  validation.value = null; validationState.value = 'idle'; validationError.value = ''
  importRefreshRequired.value = false
}

onMounted(() => {
  if (app.state === 'idle') void app.load()
  void store.listDomains().catch(() => undefined)
})

async function restoreSavedImport() {
  const actorId = app.accessContext?.actor_id
  if (!actorId || !store.domains.length) return
  const pointer = readImportPointer()
  if (!pointer) { clearImportPointer(); return }
  if (pointer.actorId !== actorId || !availableProjects.value.some((project) => project.id === pointer.projectId)
    || !store.domains.some((domain) => domain.id === pointer.domainId)) {
    restoreError.value = '保存的导入草稿属于其他访问上下文，或当前项目/数据域暂不可访问；指针已保留。'
    return
  }
  restoringImportPointer = true; restoreError.value = ''
  try {
    selectedProjectId.value = pointer.projectId
    await selectDomain(pointer.domainId)
    const expectedProjectId = pointer.projectId
    const expectedDomainId = pointer.domainId
    const generation = projectGeneration
    const isCurrent = () => generation === projectGeneration && expectedProjectId === projectId.value && expectedDomainId === domainId.value
    if (!isCurrent() || schemaState.value !== 'ready' || !schema.value) throw new Error('当前项目或数据域 schema 暂不可用。')
    const dataImport = await store.getImport(pointer.importId, isCurrent, expectedProjectId)
    if (!isCurrent()) return
    if (dataImport.domain_id !== expectedDomainId) throw new Error('已保存的导入草稿不属于当前数据域。')
    selectedImportId.value = dataImport.id
    await refreshBusinessImport({ followJob: true })
    if (importRefreshRequired.value) restoreError.value = jobError.value || '导入业务状态暂不可用；指针已保留，可稍后重试。'
  } catch (reason) {
    restoreError.value = reason instanceof Error ? `${reason.message} 导入指针已保留，可稍后重试。` : '导入草稿恢复失败；指针已保留，可稍后重试。'
  } finally { restoringImportPointer = false }
}

watch([() => app.accessContext?.actor_id, () => store.domains.length], ([actorId]) => {
  if (!actorId || !store.domains.length || restoredActorId === actorId) return
  restoredActorId = actorId
  void restoreSavedImport()
}, { immediate: true })

function retryRestoreImport() {
  restoredActorId = app.accessContext?.actor_id ?? ''
  void restoreSavedImport()
}

watch([availableProjects, () => app.accessContext], ([projects, context]) => {
  const allowed = projects.map((project) => project.id)
  if (!allowed.includes(selectedProjectId.value)) selectedProjectId.value = resolveProjectId(context, null) ?? ''
}, { immediate: true })

watch([projectId, () => app.accessContext?.actor_id], () => {
  projectGeneration += 1; schemaGeneration += 1; uploadGeneration += 1; versionGeneration += 1
  domainId.value = ''; schema.value = null; schemaState.value = 'idle'; schemaError.value = ''
  file.value = null; acceptedUpload.value = null; uploadedArtifact.value = null; uploadState.value = 'idle'; uploadMessage.value = ''
  selectedImportId.value = null; formError.value = null; actionBusy.value = false; versionLoading.value = false
  resetJobContext(); confirmations.reset()
  store.versions = []; store.versionPage = null
  selectedVersionId.value = ''; versionDetail.value = null; detailState.value = 'idle'; versionError.value = ''
}, { flush: 'sync' })

function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  acceptedUpload.value = null; uploadedArtifact.value = null; uploadState.value = 'idle'; uploadMessage.value = ''
  uploadGeneration += 1
}

function chooseProject(value: string) {
  if (!restoringImportPointer) clearImportPointer()
  selectedProjectId.value = value
}

async function selectDomain(value: string) {
  if (!restoringImportPointer) clearImportPointer()
  domainId.value = value; schema.value = null; schemaError.value = ''
  file.value = null; acceptedUpload.value = null; uploadedArtifact.value = null; uploadState.value = 'idle'; uploadMessage.value = ''
  uploadGeneration += 1; selectedImportId.value = null; formError.value = null; actionBusy.value = false; versionLoading.value = false
  resetJobContext(); confirmations.reset()
  store.versions = []; store.versionPage = null; selectedVersionId.value = ''; versionDetail.value = null; detailState.value = 'idle'; versionError.value = ''
  versionGeneration += 1
  const generation = ++schemaGeneration
  const selected = selectedDomain.value
  if (!selected) { schemaState.value = 'idle'; return }
  schemaState.value = 'loading'
  try {
    const result = await store.getDomainSchema(selected.id, selected.version)
    if (generation !== schemaGeneration || domainId.value !== selected.id) return
    if (result.id !== selected.id || result.version !== selected.version) throw new Error('服务端返回的域或 schema 版本与选择不匹配。')
    schema.value = result; schemaState.value = 'ready'
    if (!result.supported_import_modes.includes(importMode.value)) importMode.value = result.supported_import_modes[0] ?? 'append'
  } catch (reason) {
    if (generation !== schemaGeneration) return
    schemaState.value = 'error'; schemaError.value = reason instanceof Error ? reason.message : '数据域 schema 暂不可用。'
  }
}

function mediaType(selected: File) {
  const extension = selected.name.toLowerCase().split('.').pop()
  if (extension === 'xlsx') return 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  if (extension === 'tsv') return 'text/tab-separated-values'
  return selected.type || 'text/csv'
}

async function uploadFile() {
  const selected = file.value
  if (!selected || !projectId.value || !selectedDomain.value || schemaState.value !== 'ready' || domainNotReadyReason.value || uploadState.value === 'uploading') return
  const generation = ++uploadGeneration
  const contextGeneration = projectGeneration
  const currentSchemaGeneration = schemaGeneration
  const expectedProjectId = projectId.value
  const expectedDomainId = domainId.value
  const current = () => generation === uploadGeneration && contextGeneration === projectGeneration
    && currentSchemaGeneration === schemaGeneration && expectedProjectId === projectId.value
    && expectedDomainId === domainId.value && file.value === selected
  uploadState.value = 'uploading'; uploadMessage.value = '正在创建受控上传并发送文件…'; acceptedUpload.value = null; uploadedArtifact.value = null; formError.value = null
  try {
    const body: ContractCreateUploadBody = { project_id: expectedProjectId, kind: 'dataset_package', file_name: selected.name, media_type: mediaType(selected), size_bytes: selected.size }
    const created = await artifacts.createUpload(body)
    if (!current()) return
    if (!created.data.content_url) throw new Error('服务端上传会话缺少受控上传地址。')
    await artifacts.uploadContent(created.data.id, selected)
    if (!current()) return
    const accepted = await artifacts.completeUpload(created.data.id)
    if (!current()) return
    if (accepted.data.resource_ref.type !== 'artifact' || accepted.data.artifact_id !== accepted.data.resource_ref.id) throw new Error('服务端制品受理响应绑定不一致。')
    acceptedUpload.value = accepted.data
    uploadState.value = 'checking'; uploadMessage.value = '上传已受理，正在读取服务端制品状态。'
    await refreshArtifact(accepted.data.artifact_id)
  } catch (reason) {
    if (!current()) return
    uploadState.value = 'error'; uploadMessage.value = reason instanceof Error ? reason.message : '数据包上传失败。'
  }
}

async function refreshArtifact(id = acceptedUpload.value?.artifact_id) {
  if (!id || !acceptedUpload.value || acceptedUpload.value.artifact_id !== id) return
  const generation = ++uploadGeneration
  const contextGeneration = projectGeneration
  const currentSchemaGeneration = schemaGeneration
  const expectedProjectId = projectId.value
  const expectedDomainId = domainId.value
  const current = () => generation === uploadGeneration && contextGeneration === projectGeneration
    && currentSchemaGeneration === schemaGeneration && expectedProjectId === projectId.value
    && expectedDomainId === domainId.value && acceptedUpload.value?.artifact_id === id
  uploadState.value = 'checking'; uploadMessage.value = '正在读取服务端制品和扫描状态…'
  try {
    const value = await artifacts.getArtifact(id)
    if (!current()) return
    if (value.id !== id || value.project_id !== expectedProjectId || value.kind !== 'dataset_package') throw new Error('服务端制品与当前项目或数据包用途不匹配。')
    uploadedArtifact.value = value
    if (value.status === 'AVAILABLE' && value.scan_status === 'PASSED') {
      uploadState.value = 'ready'; uploadMessage.value = '制品状态 AVAILABLE，扫描 PASSED；可绑定到导入草稿。'
    } else {
      uploadState.value = 'pending'; uploadMessage.value = `制品状态 ${value.status}，扫描 ${value.scan_status}；达到 AVAILABLE 且 PASSED 后才能绑定。`
    }
  } catch (reason) {
    if (!current()) return
    uploadState.value = 'error'; uploadMessage.value = reason instanceof Error ? reason.message : '制品状态暂不可用。'
  }
}

async function createImport() {
  if (!canCreateImport.value || !schema.value) return
  formError.value = null
  let fieldMapping: Record<string, string>
  try {
    const parsed: unknown = JSON.parse(mappingJson.value)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('字段映射必须是 JSON 对象。')
    fieldMapping = parsed as Record<string, string>
  } catch (reason) { formError.value = reason instanceof Error ? reason.message : '字段映射 JSON 无效。'; return }
  const contextGeneration = projectGeneration
  const currentSchemaGeneration = schemaGeneration
  const expectedProjectId = projectId.value
  const expectedDomainId = schema.value.id
  const current = () => contextGeneration === projectGeneration && currentSchemaGeneration === schemaGeneration
    && expectedProjectId === projectId.value && expectedDomainId === domainId.value
  actionBusy.value = true
  try {
    const body: ContractDataImportCreateBody = { project_id: expectedProjectId, domain_id: schema.value.id, artifact_id: artifactId.value, schema_version: schema.value.version, import_mode: importMode.value, field_mapping: fieldMapping, source_description: sourceDescription.value }
    const result = await store.createImport(body, current)
    if (current()) {
      resetJobContext(); confirmations.reset()
      selectedImportId.value = result.data.id; persistImportPointer(result.data.id)
    }
  } catch (reason) {
    if (current()) formError.value = reason instanceof Error ? reason.message : '数据导入草稿创建失败。'
  } finally { if (current()) actionBusy.value = false }
}

function contextIsCurrent(generation: number, expectedProjectId: string, expectedDomainId: string, expectedImportId: string) {
  return generation === projectGeneration && expectedProjectId === projectId.value && expectedDomainId === domainId.value && expectedImportId === selectedImportId.value
}

function assertAcceptedImportJob(result: { data: ContractAsyncAccepted; location?: string }, expectedImportId: string) {
  const accepted = result.data
  const expectedLocation = `/api/v1/data-imports/${encodeURIComponent(expectedImportId)}`
  if (accepted.id !== expectedImportId || accepted.status !== 'QUEUED' || !accepted.job_id
    || accepted.resource_ref.type !== 'data_import' || accepted.resource_ref.id !== expectedImportId
    || result.location !== expectedLocation) {
    throw new Error('服务端异步受理响应未绑定当前导入或业务详情地址；请刷新导入状态，不要重复提交。')
  }
}

const terminalJobs = new Set(['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'])

async function loadValidationReport(validationValue: ContractDataImportValidation, dataImport: ContractDataImport, isCurrent: () => boolean) {
  resetValidationReport()
  const generation = reportGeneration
  const current = () => generation === reportGeneration && isCurrent()
  if (!validationValue.report_artifact_id) return
  reportState.value = 'loading'; reportError.value = ''
  const controller = new AbortController(); reportController = controller
  try {
    const metadata = await store.getValidationArtifact(validationValue.report_artifact_id)
    if (!current()) return
    if (metadata.id !== validationValue.report_artifact_id || metadata.project_id !== dataImport.project_id
      || metadata.kind !== 'export_report' || metadata.media_type !== 'application/json'
      || metadata.status !== 'AVAILABLE' || metadata.download_allowed !== true
      || metadata.size_bytes <= 0 || metadata.size_bytes > 2 * 1024 * 1024
      || metadata.source_ref?.type !== 'data_import_validation' || metadata.source_ref.id !== validationValue.id) {
      throw new Error('服务端报告制品未授权、超限或不属于当前 validation；未读取内容。')
    }
    const raw = await store.getValidationReport(metadata.id, controller.signal)
    if (!current()) return
    const parsed = parseDataImportValidationReport(raw)
    const sourceArtifact = await store.getValidationArtifact(parsed.source_artifact_id)
    if (!current()) return
    report.value = bindDataImportValidationReport(parsed, validationValue, dataImport, metadata, sourceArtifact)
    reportArtifact.value = metadata; reportState.value = 'ready'
  } catch (reason) {
    if (!current()) return
    reportState.value = 'error'; reportError.value = reason instanceof Error ? reason.message : '验证报告读取失败。'
  } finally { if (reportController === controller) reportController = null }
}

async function refreshBusinessImport(options: { expectedJobId?: string; expectedStatus?: ContractDataImport['status']; operation?: 'validate' | 'commit'; followJob?: boolean } = {}) {
  const currentImport = selectedImport.value
  if (!currentImport) return
  const generation = projectGeneration
  const expectedProjectId = projectId.value
  const expectedDomainId = domainId.value
  const expectedImportId = currentImport.id
  const isCurrent = () => contextIsCurrent(generation, expectedProjectId, expectedDomainId, expectedImportId)
  importRefreshRequired.value = true
  formError.value = ''; jobError.value = ''
  try {
    const refreshed = await store.getImport(expectedImportId, isCurrent, expectedProjectId)
    if (!isCurrent()) return
    if (refreshed.domain_id !== expectedDomainId) throw new Error('服务端导入记录与当前数据域不匹配。')
    if (options.expectedJobId && (refreshed.job_id !== options.expectedJobId || refreshed.status !== options.expectedStatus)) {
      throw new Error('服务端导入状态或 job_id 与受理响应不匹配；请检查业务状态。')
    }
    importRefreshRequired.value = false
    if (refreshed.status === 'FAILED') {
      stopJobPolling(); jobState.value = 'terminal'
      jobError.value = '此导入已失败或验证被取消。原 Import 不可恢复；请用有效制品创建新的导入草稿。'
    }
    if (options.followJob && refreshed.job_id && (refreshed.status === 'VALIDATING' || refreshed.status === 'COMMITTING')) {
      const operation = options.operation ?? (refreshed.status === 'VALIDATING' ? 'validate' : 'commit')
      const kind = operation === 'validate' ? 'data_import_validation' : 'data_import_commit'
      if (options.expectedJobId && refreshed.job_id !== options.expectedJobId) throw new Error('导入记录当前 job_id 与已受理 job 不一致。')
      if (!jobId.value) { jobId.value = refreshed.job_id; jobOperation.value = operation; jobAttempt = null }
      if (jobId.value !== refreshed.job_id || jobOperation.value !== operation) throw new Error('当前导入存在另一项运行任务，停止跟随以免混淆。')
      await pollImportJob(generation, expectedProjectId, expectedDomainId, expectedImportId, refreshed.job_id, kind)
      return
    }
    if (['DRAFT', 'READY', 'INVALID', 'FAILED', 'COMMITTED'].includes(refreshed.status)) {
      validationState.value = 'loading'; validationError.value = ''; validation.value = null
      resetValidationReport()
      try {
        const value = await store.getValidation(expectedImportId, isCurrent, expectedProjectId, {
          id: refreshed.validation_ref ?? undefined, revision: refreshed.revision, configHash: refreshed.config_hash, allowStale: true,
        })
        if (!isCurrent()) return
        if (value.status !== 'STALE' && (!refreshed.validation_ref || value.id !== refreshed.validation_ref
          || value.revision !== refreshed.revision || value.config_hash !== refreshed.config_hash)) {
          throw new Error('非 STALE validation 必须绑定当前导入 revision/config 和 validation_ref。')
        }
        validation.value = value; validationState.value = 'ready'
        await loadValidationReport(value, refreshed, isCurrent)
      } catch (reason) {
        if (!isCurrent()) return
        if (!refreshed.validation_ref && reason instanceof ApiClientError && reason.status === 404) {
          validationState.value = 'idle'; validationError.value = ''; validation.value = null
        } else {
          validationState.value = 'error'; validationError.value = reason instanceof Error ? reason.message : '验证详情读取失败。'
        }
      }
    }
    if (refreshed.status === 'COMMITTED') {
      await showCommittedVersion(refreshed.new_data_version_id, expectedImportId, expectedProjectId, expectedDomainId, isCurrent)
    }
  } catch (reason) {
    if (isCurrent()) { importRefreshRequired.value = true; jobState.value = 'error'; jobError.value = reason instanceof Error ? reason.message : '导入业务状态刷新失败。' }
  }
}

async function pollImportJob(generation: number, expectedProjectId: string, expectedDomainId: string, expectedImportId: string, expectedJobId: string, kind: 'data_import_validation' | 'data_import_commit') {
  const pollingGeneration = jobGeneration
  const isCurrent = () => pollingGeneration === jobGeneration && contextIsCurrent(generation, expectedProjectId, expectedDomainId, expectedImportId) && jobId.value === expectedJobId
  if (!isCurrent()) return
  jobState.value = 'checking'; jobError.value = ''
  try {
    const loaded = await store.getBoundJob(expectedJobId, expectedProjectId, expectedImportId, kind)
    if (!isCurrent()) return
    if (jobAttempt !== null && loaded.attempt !== jobAttempt) throw new Error('Job attempt 已变化；已停止自动跟随。请刷新业务状态后确认当前任务。')
    jobAttempt ??= loaded.attempt
    job.value = loaded
    if (terminalJobs.has(loaded.status)) {
      if (jobTimer !== null) window.clearTimeout(jobTimer)
      jobTimer = null; jobState.value = 'refreshing'
      const refreshed = await store.getImport(expectedImportId, isCurrent, expectedProjectId)
      if (!isCurrent()) return
      if (refreshed.domain_id !== expectedDomainId || refreshed.job_id !== expectedJobId) throw new Error('终态 Job 与刷新后的导入记录绑定不一致。')
      validationState.value = 'loading'
      try {
        const value = await store.getValidation(expectedImportId, isCurrent, expectedProjectId, {
          id: refreshed.validation_ref ?? undefined, revision: refreshed.revision, configHash: refreshed.config_hash, allowStale: true,
        })
        if (!isCurrent()) return
        if (value.import_id !== expectedImportId || (value.status !== 'STALE' && (!refreshed.validation_ref || value.id !== refreshed.validation_ref
          || value.revision !== refreshed.revision || value.config_hash !== refreshed.config_hash))) throw new Error('非 STALE 终态 validation 必须绑定当前导入 revision/config 和 validation_ref。')
        validation.value = value; validationState.value = 'ready'; validationError.value = ''
        await loadValidationReport(value, refreshed, isCurrent)
      } catch (reason) {
        if (!isCurrent()) return
        validationState.value = 'error'; validationError.value = reason instanceof Error ? reason.message : '验证详情读取失败。'
      }
      jobState.value = 'terminal'; importRefreshRequired.value = false
      if (refreshed.status === 'FAILED') jobError.value = '此导入已失败或验证被取消。原 Import 不可恢复；请创建新的导入草稿。'
      if (kind === 'data_import_commit' && loaded.status === 'SUCCEEDED' && refreshed.status === 'COMMITTED') {
        await showCommittedVersion(refreshed.new_data_version_id, expectedImportId, expectedProjectId, expectedDomainId, isCurrent)
      }
      return
    }
    jobState.value = 'polling'
    jobTimer = window.setTimeout(() => { void pollImportJob(generation, expectedProjectId, expectedDomainId, expectedImportId, expectedJobId, kind) }, 2000)
  } catch (reason) {
    if (!isCurrent()) return
    jobState.value = 'error'; jobError.value = reason instanceof Error ? reason.message : 'Job 状态读取失败；可安全刷新业务状态。'
  }
}

async function startImportJob(operation: 'validate' | 'commit') {
  const currentImport = selectedImport.value
  if (!currentImport || actionBusy.value || jobBusy.value || importRefreshRequired.value) return
  if (operation === 'commit' && (!canConfirmCommit.value || !confirmations.confirmation || !confirmationMatches.value)) return
  if (operation === 'validate' && (!['DRAFT', 'INVALID', 'READY'].includes(currentImport.status) || !currentImport.etag)) return
  const expectedProjectId = projectId.value
  const expectedDomainId = domainId.value
  const expectedImportId = currentImport.id
  const contextGeneration = projectGeneration
  const isCurrent = () => contextIsCurrent(contextGeneration, expectedProjectId, expectedDomainId, expectedImportId)
  stopJobPolling(); resetValidationReport()
  jobAttempt = null; job.value = null; jobError.value = ''; validation.value = operation === 'validate' ? null : validation.value
  validationState.value = operation === 'validate' ? 'idle' : validationState.value
  validationError.value = ''; jobOperation.value = operation; jobState.value = 'checking'
  const confirmationId = operation === 'commit' ? confirmations.confirmation!.id : null
  if (operation === 'validate' || operation === 'commit') confirmations.reset()
  actionBusy.value = true; formError.value = ''; importRefreshRequired.value = false
  let postReturned = false
  try {
    const result = operation === 'validate'
      ? await store.validateImport(expectedImportId, currentImport.etag, expectedProjectId)
      : await store.commitImport(expectedImportId, confirmationId!, expectedProjectId)
    postReturned = true
    if (!isCurrent()) return
    assertAcceptedImportJob(result, expectedImportId)
    jobId.value = result.data.job_id
    const expectedStatus = operation === 'validate' ? 'VALIDATING' : 'COMMITTING'
    const refreshed = await store.getImport(expectedImportId, isCurrent, expectedProjectId)
    if (!isCurrent()) return
    const terminalStatus = operation === 'validate' ? ['READY', 'INVALID', 'FAILED'].includes(refreshed.status)
      : ['COMMITTED', 'FAILED', 'READY'].includes(refreshed.status)
    if (refreshed.domain_id !== expectedDomainId || refreshed.job_id !== result.data.job_id || (refreshed.status !== expectedStatus && !terminalStatus)) {
      throw new Error('202受理后业务 GET 未显示对应的活动或终态任务；请刷新状态，不要重复提交。')
    }
    await pollImportJob(contextGeneration, expectedProjectId, expectedDomainId, expectedImportId, result.data.job_id, operation === 'validate' ? 'data_import_validation' : 'data_import_commit')
  } catch (reason) {
    if (isCurrent()) {
      const rejected = !postReturned && reason instanceof ApiClientError && reason.status >= 400 && reason.status < 500 && reason.status !== 408 && reason.status !== 429
      importRefreshRequired.value = !rejected
      jobState.value = 'error'; jobError.value = rejected
        ? `服务端明确拒绝了本次${operation === 'validate' ? '校验' : '提交'}请求（${reason.code}）；请刷新业务状态后再决定下一步。`
        : reason instanceof Error ? `${reason.message} 服务端是否已受理未知；请只刷新业务状态，不要重复提交。` : '服务端受理状态未知。请只刷新业务状态，不要重复提交。'
    }
  } finally { if (isCurrent()) actionBusy.value = false }
}

async function validateImport() { await startImportJob('validate') }

async function refreshSelectedImport() {
  stopJobPolling()
  jobState.value = 'idle'
  await refreshBusinessImport({ followJob: true })
}

async function commitImport() {
  if (!canConfirmCommit.value || !confirmationMatches.value) return
  await startImportJob('commit')
}

async function loadVersions() {
  if (!projectId.value || !domainId.value) return
  const generation = ++versionGeneration
  const contextGeneration = projectGeneration
  const expectedProjectId = projectId.value
  const expectedDomainId = domainId.value
  const current = () => generation === versionGeneration && contextGeneration === projectGeneration && expectedProjectId === projectId.value && expectedDomainId === domainId.value
  versionLoading.value = true; versionError.value = ''; selectedVersionId.value = ''; versionDetail.value = null; detailState.value = 'idle'
  try { await store.listVersions(expectedProjectId, expectedDomainId, undefined, current) }
  catch (reason) { if (current()) versionError.value = reason instanceof Error ? reason.message : '数据版本暂不可用。' }
  finally { if (current()) versionLoading.value = false }
}

async function showCommittedVersion(versionId: string | null | undefined, expectedImportId: string, expectedProjectId: string, expectedDomainId: string, isCurrent: () => boolean) {
  if (!versionId) {
    if (isCurrent()) {
      versionGeneration += 1
      selectedVersionId.value = ''; versionDetail.value = null; detailState.value = 'error'
      versionError.value = '导入状态为 COMMITTED，但服务端未提供 new_data_version_id；无法确认已生成的不可变版本。'
    }
    return
  }
  await loadVersions()
  if (!isCurrent()) return
  const generation = versionGeneration
  selectedVersionId.value = versionId; versionDetail.value = null; detailState.value = 'loading'; versionError.value = ''
  try {
    const result = await store.getVersion(versionId)
    if (!isCurrent() || generation !== versionGeneration) return
    if (result.id !== versionId || result.project_id !== expectedProjectId || result.domain_id !== expectedDomainId || result.source_import_id !== expectedImportId) throw new Error('提交结果指向的版本与当前项目、数据域或来源导入不匹配。')
    versionDetail.value = result; detailState.value = 'ready'
  } catch (reason) {
    if (isCurrent() && generation === versionGeneration) { detailState.value = 'error'; versionError.value = reason instanceof Error ? reason.message : '提交后的数据版本详情暂不可用。' }
  }
}

async function loadVersion(version: ContractDataVersion) {
  const generation = ++versionGeneration
  const contextGeneration = projectGeneration
  const expectedProjectId = projectId.value
  const current = () => generation === versionGeneration && contextGeneration === projectGeneration && expectedProjectId === projectId.value
  selectedVersionId.value = version.id; versionDetail.value = null; detailState.value = 'loading'; versionError.value = ''
  try {
    const result = await store.getVersion(version.id)
    if (!current()) return
    if (result.id !== version.id || result.project_id !== expectedProjectId || result.domain_id !== domainId.value) throw new Error('版本详情与当前选择的项目或数据域不匹配。')
    versionDetail.value = result; detailState.value = 'ready'
  } catch (reason) { if (current()) { detailState.value = 'error'; versionError.value = reason instanceof Error ? reason.message : '版本详情暂不可用。' } }
}

onBeforeUnmount(() => { projectGeneration += 1; schemaGeneration += 1; uploadGeneration += 1; versionGeneration += 1; stopJobPolling(); resetValidationReport() })
</script>

<template>
  <div class="view data-center-view">
    <header class="page-header"><span class="eyebrow">DATA & KNOWLEDGE · DATASETS</span><h1>色谱数据与版本</h1><p>选择访问上下文中的项目、服务端数据域和 schema；导入草稿仍由服务端校验，提交需要绑定服务端确认。</p></header>
    <section v-if="availableProjects.length" class="panel project-section"><label>项目<select :value="selectedProjectId" data-testid="datasets-project" @change="chooseProject(($event.target as HTMLSelectElement).value)"><option value="" disabled>选择服务端访问上下文中的项目</option><option v-for="project in availableProjects" :key="project.id" :value="project.id">{{ project.name || project.id }}</option></select></label></section>
    <p v-if="app.state === 'idle' || app.state === 'loading'" class="panel placeholder" role="status">正在读取访问上下文…</p>
    <p v-else-if="app.state === 'error' && !app.accessContext" class="panel error-text" role="alert">访问上下文暂不可用，数据操作等待连接后使用。{{ app.error }}</p>
    <p v-else-if="projectState === 'blocked'" class="panel error-text" role="status">当前访问上下文没有可访问项目，数据操作不可用。</p>
    <p v-else-if="projectState === 'needs_selection'" class="panel error-text" role="status">请选择访问上下文中的项目后继续。</p>
    <template v-else-if="projectId">
      <section class="panel">
        <h2>可用数据域</h2>
        <div v-if="store.state === 'loading'" class="placeholder" role="status">正在读取数据域…</div><div v-else-if="store.state === 'error'" class="error-text" role="alert">{{ store.error }}</div><div v-else-if="!store.domains.length" class="placeholder">服务端未返回数据域；当前没有可用导入目标。</div>
        <label v-else>数据域<select :value="domainId" data-testid="datasets-domain" @change="selectDomain(($event.target as HTMLSelectElement).value)"><option value="">选择服务端数据域</option><option v-for="domain in store.domains" :key="domain.id" :value="domain.id">{{ domain.chromatography_type.toUpperCase() }} · v{{ domain.version }}</option></select></label>
        <p v-if="selectedDomain && schemaState === 'loading'" class="placeholder" role="status">正在读取 {{ selectedDomain.chromatography_type.toUpperCase() }} schema…</p>
        <p v-if="schemaState === 'error'" class="error-text" role="alert">{{ schemaError }}</p>
        <template v-if="schemaState === 'ready' && schema">
          <p class="muted">服务端 schema v{{ schema.version }}；字段定义和导入模式来自服务端。是否适用于实际科学数据由服务端导入校验决定。</p>
          <p v-if="domainNotReadyReason" class="error-text" role="status">当前数据域不可构造导入请求：{{ domainNotReadyReason }}</p>
          <dl v-else class="facts"><div><dt>色谱类型</dt><dd>{{ schema.chromatography_type.toUpperCase() }}</dd></div><div><dt>身份字段</dt><dd>{{ schema.identity_fields.join('、') }}</dd></div><div><dt>去重策略</dt><dd>{{ schema.deduplication_policy }}</dd></div><div><dt>可用导入模式</dt><dd>{{ schema.supported_import_modes.join('、') }}</dd></div></dl>
          <div v-if="schemaFields.length" class="event-list"><div v-for="[name, field] in schemaFields" :key="name"><strong>{{ name }}</strong><span> · {{ field.type }}<template v-if="field.unit"> · {{ field.unit }}</template><template v-if="field.required"> · 必填</template></span></div></div>
        </template>
      </section>

      <section class="panel"><h2>上传数据包</h2><p class="muted">文件先通过受控制品上传；只有服务端状态为 AVAILABLE 且扫描 PASSED 时，才会绑定到导入草稿。</p>
        <label>数据文件<input data-testid="datasets-file" type="file" accept=".csv,.tsv,.xlsx" :disabled="!schema || Boolean(domainNotReadyReason)" @change="chooseFile" /></label>
        <p v-if="file" class="muted">已选择 {{ file.name }} · {{ file.size }} 字节</p>
        <button class="primary" data-testid="datasets-upload" :disabled="!file || !projectId || !schema || Boolean(domainNotReadyReason) || uploadState === 'uploading'" @click="uploadFile">{{ uploadState === 'uploading' ? '上传中…' : '上传并读取服务端状态' }}</button>
        <p v-if="uploadMessage" :class="uploadState === 'error' ? 'error-text' : 'muted'" role="status">{{ uploadMessage }}</p>
        <p v-if="uploadedArtifact" class="muted">服务端制品：{{ uploadedArtifact.status }} · 扫描 {{ uploadedArtifact.scan_status }}</p>
        <button v-if="acceptedUpload && !artifactReady" class="secondary" :disabled="uploadState === 'checking'" @click="refreshArtifact()">{{ uploadState === 'checking' ? '读取中…' : '刷新制品状态' }}</button>
      </section>

      <section class="panel"><h2>创建导入草稿</h2><div class="grid two-column">
        <label>Schema 版本<select :value="schema?.version ?? ''" disabled><option v-for="version in schemaVersions" :key="version" :value="version">{{ version }}</option></select></label>
        <label>导入模式<select v-model="importMode" :disabled="!schema"><option v-for="mode in schema?.supported_import_modes ?? []" :key="mode" :value="mode">{{ mode === 'append' ? '追加' : '更新已有' }}</option></select></label>
        <label class="wide-field">来源描述<input v-model="sourceDescription" /></label>
      </div><label class="wide-field">字段映射 JSON<textarea v-model="mappingJson" rows="4" spellcheck="false" /></label>
        <p v-if="!artifactReady" class="muted">完成制品服务校验和扫描后才能创建草稿。</p>
        <button class="primary" data-testid="datasets-create-import" :disabled="!canCreateImport" @click="createImport">{{ actionBusy ? '提交中…' : '创建导入草稿' }}</button>
      </section>
      <section v-if="selectedImport" class="panel"><h2>导入草稿</h2><dl class="facts"><div><dt>状态</dt><dd>{{ selectedImport.status }}</dd></div><div><dt>revision</dt><dd>{{ selectedImport.revision }}</dd></div><div><dt>ETag</dt><dd>{{ selectedImport.etag }}</dd></div></dl>
        <button class="secondary" :disabled="actionBusy || jobBusy || !['DRAFT', 'INVALID', 'READY'].includes(selectedImport.status) || !selectedImport.etag" @click="validateImport">执行服务端校验</button>
        <button class="secondary" :disabled="actionBusy || jobBusy" @click="refreshSelectedImport">{{ jobBusy ? '刷新中…' : '刷新导入状态' }}</button>
        <p v-if="jobOperation" class="muted" role="status">{{ jobOperation === 'validate' ? '服务端校验任务' : '服务端提交任务' }}：{{ jobState }}<template v-if="job"> · {{ statusLabel(job.status) }} · attempt {{ job.attempt }}</template></p>
        <p v-if="jobError" class="error-text" role="alert">{{ jobError }}</p>
        <p v-if="importRefreshRequired" class="error-text" role="status">提交结果可能未知。只刷新业务状态不会重复提交。</p>
        <p v-if="validationState === 'loading'" class="placeholder" role="status">正在读取服务端 validation…</p>
        <p v-else-if="validationState === 'error'" class="error-text" role="alert">{{ validationError }}</p>
        <p v-else-if="validationState === 'idle' && !selectedImport.validation_ref" class="muted">当前草稿尚无关联 validation。</p>
        <template v-else-if="validationState === 'ready' && validation">
          <p class="muted">Validation {{ validation.status }}；校验差异预计：拟新增 {{ validation.counts.new }}、拟更新 {{ validation.counts.updated }}、未变 {{ validation.counts.unchanged }}、无效 {{ validation.counts.invalid }}、重复 {{ validation.counts.duplicate }}。这些是校验计数，不是实际写入数；已提交数量以数据版本 record_count 为准。</p>
          <p v-if="!validation.report_artifact_id" class="muted">服务端未提供验证报告制品。</p>
          <p v-if="reportState === 'loading'" class="placeholder" role="status">正在校验并读取有界验证报告…</p>
          <p v-else-if="reportState === 'error'" class="error-text" role="alert">{{ reportError }}</p>
          <template v-else-if="reportState === 'ready' && report && reportArtifact">
            <p v-if="report.historical" class="muted">这是所属 validation 的历史报告，不代表当前 revision 可提交。</p>
            <p class="muted">报告状态 {{ report.report.status }} · 报告制品 {{ reportArtifact.status }} · 扫描 {{ reportArtifact.scan_status }}<template v-if="reportArtifact.scan_status === 'NOT_CONFIGURED'">（扫描未配置，不代表输入通过扫描）</template></p>
            <p v-if="report.report.status === 'VALID' && report.report.total_issue_count === 0" class="muted">该 VALID 报告列出 0 条问题；这是校验结果，不代表已提交。</p>
            <p class="muted">完整问题数 {{ report.report.total_issue_count }}；当前显示 {{ report.report.issues.length }} 条。报告校验计数：拟新增 {{ report.report.counts.new }}、拟更新 {{ report.report.counts.updated }}、未变 {{ report.report.counts.unchanged }}、无效 {{ report.report.counts.invalid }}、重复 {{ report.report.counts.duplicate }}；这不是实际写入数。</p>
            <p v-if="report.report.issues_truncated" class="error-text" role="status">问题列表已截断；未显示的记录不能据此视为有效。</p>
            <div v-if="report.report.issues.length" class="event-list"><article v-for="(issue, index) in report.report.issues" :key="`${issue.source_row_number}-${issue.code}-${index}`"><strong>CSV 物理行 {{ issue.source_row_number }} · {{ issue.code }}</strong><p>{{ issue.message }}</p><p class="muted">字段 {{ issue.field || '未指定' }} · 测量 ID {{ issue.measurement_id || '未指定' }}</p><p class="muted">原值预览：{{ issue.raw_value_preview }}<template v-if="issue.raw_value_truncated">（已截断）</template></p><p v-if="issue.raw_value_sha256" class="muted">完整原值 SHA-256：{{ issue.raw_value_sha256 }}</p></article></div>
          </template>
        </template>
        <template v-if="canConfirmCommit && importRef"><ConfirmationCard :key="`${selectedImport.id}:${selectedImport.revision}:${selectedImport.config_hash}`" :resource-ref="importRef" action="data_import.commit" /><button class="primary" :disabled="!confirmationMatches" @click="commitImport">提交导入（需服务端校验与确认）</button></template>
      </section>
      <p v-if="formError" class="error-text" role="alert">{{ formError }}</p>
      <section class="panel"><span class="eyebrow">IMMUTABLE DATA VERSIONS</span><h2>数据版本</h2><button class="secondary" data-testid="datasets-load-versions" :disabled="versionLoading || !domainId" @click="loadVersions">{{ versionLoading ? '读取中…' : '读取服务端版本列表' }}</button>
        <p v-if="versionError && !store.versions.length" class="error-text" role="alert">{{ versionError }}</p><p v-else-if="!versionLoading && !versionError && !store.versions.length && domainId" class="placeholder">当前项目和数据域没有可见版本。</p>
        <div v-if="store.versions.length" class="event-list"><button v-for="version in store.versions" :key="version.id" class="secondary" :aria-pressed="selectedVersionId === version.id" @click="loadVersion(version)">v{{ version.version }} · {{ version.status }} · {{ version.record_count }} 条记录</button></div>
        <p v-if="detailState === 'loading'" class="placeholder" role="status">正在读取版本详情…</p><p v-else-if="detailState === 'error'" class="error-text" role="alert">{{ versionError }}</p>
        <p v-if="detailState === 'ready' && versionDetail && selectedImport?.status === 'COMMITTED' && selectedImport.new_data_version_id === versionDetail.id && versionDetail.record_count > 0" class="muted" role="status">提交完成：该不可变版本实际包含 {{ versionDetail.record_count }} 条记录。</p>
        <p v-else-if="detailState === 'ready' && versionDetail && selectedImport?.status === 'COMMITTED' && selectedImport.new_data_version_id === versionDetail.id && versionDetail.record_count === 0" class="error-text" role="alert">服务端返回的提交版本记录数为 0，不符合本阶段的非空版本要求。</p>
        <dl v-if="detailState === 'ready' && versionDetail" class="facts"><div><dt>版本</dt><dd>v{{ versionDetail.version }}</dd></div><div><dt>状态</dt><dd>{{ versionDetail.status }}</dd></div><div><dt>Schema</dt><dd>{{ versionDetail.schema_version }}</dd></div><div><dt>SHA-256</dt><dd>{{ versionDetail.sha256 }}</dd></div><div><dt>实际记录数</dt><dd>{{ versionDetail.record_count }}</dd></div><div><dt>来源导入</dt><dd>{{ versionDetail.source_import_id }}</dd></div><div><dt>快照制品引用</dt><dd>{{ versionDetail.snapshot_artifact_id }}</dd></div><div><dt>质量报告引用</dt><dd>{{ versionDetail.quality_report_ref ? `${versionDetail.quality_report_ref.type} · ${versionDetail.quality_report_ref.id}` : '未提供' }}</dd></div><div><dt>引用数</dt><dd>{{ versionDetail.referenced_by_count }}</dd></div><div><dt>创建时间</dt><dd>{{ versionDetail.created_at }}</dd></div></dl>
        <p v-if="versionError && store.versions.length" class="error-text" role="alert">{{ versionError }}</p>
      </section>
    </template>
    <section v-if="restoreError" class="panel error-text" role="status"><p>{{ restoreError }}</p><button class="secondary" @click="retryRestoreImport">重试恢复保存的导入草稿</button></section>
  </div>
</template>
