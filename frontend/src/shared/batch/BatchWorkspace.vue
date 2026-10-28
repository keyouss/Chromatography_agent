<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ApiClientError } from '@/core/api/client'
import { useArtifactStore } from '@/stores/artifacts'
import { useConfirmationStore } from '@/stores/confirmations'
import { useJobStore } from '@/stores/jobs'
import JobStatus from '@/components/JobStatus.vue'
import BatchStructurePreparation from './BatchStructurePreparation.vue'
import { createBatchClient } from './batch-client'
import {
  batchWorkspaceKey,
  readBatchWorkspace,
  writeBatchWorkspace,
} from './batch-workspace'
import type {
  BatchAccepted,
  BatchChromatographyType,
  BatchDraft,
  BatchDraftCreate,
  BatchExportCreate,
  BatchExportView,
  BatchModelOption,
  BatchResourceBudget,
  BatchRetryPlan,
  BatchRowResult,
  BatchTemplateDefinition,
  BatchToolCapability,
  BatchTypeCapability,
  BatchValidation,
  BatchResolvedInput,
  RowValidation,
  TlcBatchToolCapability,
  NonTlcBatchToolCapability,
  TlcBatchCompatibleModel,
  NonTlcBatchCompatibleModel,
} from './batch-types'
import { formatResultCell } from '@/shared/prediction/result'
import type { ContractCreateUploadBody } from '@/core/contract-types'
import type { Artifact, ArtifactAccepted, Job, ResourceRef } from '@/core/types'

type CompatibleModel = TlcBatchCompatibleModel | NonTlcBatchCompatibleModel
type BatchCapableTool = TlcBatchToolCapability | NonTlcBatchToolCapability
type ProfileBinding = { preprocessing_profile_id?: string | null; preprocessing_profile_sha256?: string | null }
type FormDraft = {
  selectedModelKey: string
  name: string
  invalidRowPolicy: 'block' | 'exclude_invalid'
  budget: BatchResourceBudget
  templateFormat: 'csv' | 'xlsx'
}

const props = withDefaults(defineProps<{
  actorId: string
  projectId: string
  chromatographyType: BatchChromatographyType
  initialBatchId?: string
  canReadBatch: boolean
  canUploadBatch: boolean
  canCreateBatch: boolean
  canCancelBatch: boolean
  policyRevision?: string
  permissionRevision?: string
}>(), { policyRevision: '', permissionRevision: '' })

const emit = defineEmits<{ 'update:formDraft': [draft: FormDraft] }>()
const artifacts = useArtifactStore()
const confirmations = useConfirmationStore()
const jobs = useJobStore()
const capability = ref<BatchTypeCapability | null>(null)
const toolCapability = ref<BatchCapableTool | null>(null)
const toolCapabilityModelKey = ref('')
const selectedModelKey = ref('')
const selectedFile = ref<File | null>(null)
const accepted = ref<ArtifactAccepted | null>(null)
const artifact = ref<Artifact | null>(null)
const draft = ref<BatchDraft | null>(null)
const validation = ref<BatchValidation | null>(null)
const validationRows = ref<RowValidation[]>([])
const resultRows = ref<BatchRowResult[]>([])
const retryPlan = ref<BatchRetryPlan | null>(null)
const selectedRetryRows = ref<string[]>([])
const exportView = ref<BatchExportView | null>(null)
const activeJobId = ref('')
const verifiedJobKey = ref('')
const verifiedJob = ref<Job | null>(null)
const recoverableDraftId = ref('')
const name = ref('')
const invalidRowPolicy = ref<'block' | 'exclude_invalid'>('block')
const maxEvaluations = ref(1)
const maxRuntimeSeconds = ref(1)
const templateFormat = ref<'csv' | 'xlsx'>('csv')
const exportFormat = ref<BatchExportCreate['format']>('csv')
const downloadedTemplateVersion = ref('')
const submissionLocked = ref(false)
const loading = ref(false)
const downloadingTemplate = ref(false)
const uploading = ref(false)
const refreshingArtifact = ref(false)
const creatingDraft = ref(false)
const patchingDraft = ref(false)
const validating = ref(false)
const refreshingValidation = ref(false)
const restoring = ref(false)
const loadingConfirmation = ref(false)
const creatingConfirmation = ref(false)
const submitting = ref(false)
const canceling = ref(false)
const refreshingResults = ref(false)
const creatingRetryPlan = ref(false)
const loadingRetryConfirmation = ref(false)
const retryingFailed = ref(false)
const creatingExport = ref(false)
const refreshingExport = ref(false)
const downloadingExport = ref(false)
const error = ref('')
const validationError = ref('')
const fileInput = ref<HTMLInputElement | null>(null)
const generation = ref(0)
const toolGeneration = ref(0)
const operationEpoch = ref(0)
const confirmationHash = ref('')
const retryConfirmationHash = ref('')

const client = computed(() => props.actorId && props.projectId
  ? createBatchClient({ actorId: props.actorId, projectId: props.projectId, type: props.chromatographyType, tool: 'batch' })
  : null)
const workspaceKey = computed(() => props.actorId && props.projectId
  ? batchWorkspaceKey(props.actorId, props.projectId, props.chromatographyType, 'batch')
  : '')
const selectedModel = computed(() => capability.value?.models.find((item) => modelKey(item) === selectedModelKey.value) ?? null)
const models = computed(() => capability.value?.models ?? [])
const matchingModels = computed(() => selectedModel.value
  ? models.value.filter((item) => modelKey(item) === modelKey(selectedModel.value!)) : [])
const batchAdvertisements = computed(() => capability.value?.tools.filter(isBatchTool) ?? [])
const advertisedBatchTool = computed(() => {
  const matches = batchAdvertisements.value.filter((item) => compatibleModel(item, selectedModel.value))
  return matches.length === 1 ? matches[0] : null
})
const advertisedCompatibility = computed(() => compatibleModel(advertisedBatchTool.value, selectedModel.value))
const selectedCompatibility = computed(() => compatibleModel(toolCapability.value, selectedModel.value))
const selectedTemplate = computed<BatchTemplateDefinition | null>(() => {
  const tool = toolCapability.value
  if (!tool || toolCapabilityModelKey.value !== selectedModelKey.value) return null
  const matches = tool.templates.filter((item) => item.tool === 'batch' && item.chromatography_type === props.chromatographyType
    && item.health.status === 'READY' && item.version === tool.template_version)
  return matches.length === 1 ? matches[0] : null
})
const toolLimits = computed(() => {
  const values = [toolCapability.value?.limits, advertisedBatchTool.value?.limits, selectedTemplate.value?.limits]
  if (values.some((value) => !hasBudget(value))) return null
  return {
    max_evaluations: Math.min(...values.map((value) => value!.max_evaluations)),
    max_runtime_seconds: Math.min(...values.map((value) => value!.max_runtime_seconds)),
  }
})
const capabilityReady = computed(() => Boolean(capability.value?.id === props.chromatographyType
  && capability.value.health.status === 'READY' && predictionLimitsReady(capability.value.limits)))
const readyTool = computed(() => {
  const model = selectedModel.value
  const tool = toolCapability.value
  const advertised = advertisedBatchTool.value
  const compatibility = selectedCompatibility.value
  const parentCompatibility = advertisedCompatibility.value
  const template = selectedTemplate.value
  if (!model || !tool || !advertised || !template || !compatibility || !parentCompatibility || !capabilityReady.value) return false
  const childVersions = tool.model_constraints.template_versions
  const parentVersions = advertised.model_constraints.template_versions
  const versionsMatch = childVersions.length === parentVersions.length && childVersions.every((version) => parentVersions.includes(version))
  const exactSupport = compatibility.model_id === model.id && compatibility.model_version === model.version
    && compatibility.input_schema_version === model.input_schema_version && compatibility.health.status === 'READY'
    && parentCompatibility.model_id === model.id && parentCompatibility.model_version === model.version
    && parentCompatibility.input_schema_version === model.input_schema_version && parentCompatibility.health.status === 'READY'
    && sameProfile(compatibility, model) && sameProfile(parentCompatibility, model)
  const profileRequired = props.chromatographyType === 'hplc' || props.chromatographyType === 'gc'
  const exactProfile = !profileRequired || Boolean(model.preprocessing_profile_id && isSha256(model.preprocessing_profile_sha256))
  return model.health.status === 'READY' && matchingModels.value.length === 1 && exactSupport && exactProfile
    && tool.id === 'batch' && tool.chromatography_type === props.chromatographyType && tool.health.status === 'READY'
    && advertised.health.status === 'READY' && tool.schema_version === advertised.schema_version && versionsMatch
    && childVersions.includes(template.version) && parentVersions.includes(template.version)
    && template.fields.length > 0 && hasBudget(tool.limits) && hasBudget(advertised.limits) && hasBudget(template.limits)
    && Boolean(toolLimits.value)
})
const requiredTemplateVersion = computed(() => ({ tlc: 'tlc-batch-2', cc: 'cc-batch-2', hplc: 'hplc-batch-2', gc: 'gc-batch-1', sfc: 'sfc-batch-2' }[props.chromatographyType]))
const templateIsWritable = computed(() => readyTool.value && selectedTemplate.value?.version === requiredTemplateVersion.value)
const isLegacyTlc = computed(() => props.chromatographyType === 'tlc' && (selectedTemplate.value?.version ?? draft.value?.template_version) === 'tlc-batch-1')
const busy = computed(() => [loading.value, downloadingTemplate.value, uploading.value, refreshingArtifact.value, creatingDraft.value,
  patchingDraft.value, validating.value, refreshingValidation.value, restoring.value, loadingConfirmation.value,
  creatingConfirmation.value, submitting.value, canceling.value, refreshingResults.value, creatingRetryPlan.value,
  loadingRetryConfirmation.value, retryingFailed.value, creatingExport.value, refreshingExport.value, downloadingExport.value].some(Boolean))
const artifactReady = computed(() => {
  const value = artifact.value
  return Boolean(value && value.id && value.project_id === props.projectId && value.kind === 'batch_input'
    && value.status === 'AVAILABLE' && value.scan_status === 'PASSED')
})
const artifactBlockerMessage = computed(() => {
  const value = artifact.value
  if (!value) return '正在等待读取服务端制品状态，尚不能用于预测。'
  if (['QUARANTINED', 'FAILED', 'ARCHIVED', 'DELETING', 'DELETED'].includes(value.status)) {
    return `${value.scan_status === 'PASSED' ? '扫描已通过，但' : ''}制品状态为 ${value.status}，不能用于预测。请查看服务端校验任务与错误信息。`
  }
  if (['REJECTED', 'FAILED', 'NOT_CONFIGURED'].includes(value.scan_status)) return `扫描状态为 ${value.scan_status}，制品不能用于预测。`
  if (value.scan_status === 'PASSED') return value.status === 'AVAILABLE'
    ? '制品引用与当前项目或批量输入用途不匹配，不能用于预测。'
    : `扫描已通过，制品仍处于 ${value.status}，达到 AVAILABLE 后才能用于预测。`
  return '制品尚未就绪，正在等待服务端解析、校验与扫描完成。'
})
const budgetLimits = computed(() => toolLimits.value)
const validBudget = computed(() => Boolean(budgetLimits.value && Number.isInteger(maxEvaluations.value)
  && maxEvaluations.value > 0 && maxEvaluations.value <= budgetLimits.value.max_evaluations
  && Number.isInteger(maxRuntimeSeconds.value) && maxRuntimeSeconds.value > 0
  && maxRuntimeSeconds.value <= budgetLimits.value.max_runtime_seconds))
const canDownloadTemplate = computed(() => Boolean(readyTool.value && templateIsWritable.value && selectedModel.value
  && !busy.value && !draft.value && !submissionLocked.value))
const canSelectFile = computed(() => Boolean(props.projectId && props.canUploadBatch && canDownloadTemplate.value
  && downloadedTemplateVersion.value === selectedTemplate.value?.version && !draft.value && !busy.value))
const canCreateDraft = computed(() => Boolean(props.canCreateBatch && props.canReadBatch && templateIsWritable.value
  && artifactReady.value && accepted.value && selectedFile.value && !draft.value && validBudget.value && !busy.value && !submissionLocked.value))
function jobBindingKey(value: BatchDraft | null) {
  return value?.job_id ? [props.projectId, value.id, value.validation_id ?? '', value.job_id].join(':') : ''
}
function isTerminalJob(status: Job['status'] | undefined) {
  return ['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'].includes(status ?? '')
}
const validationJobTerminal = computed(() => {
  const value = draft.value; const job = verifiedJob.value
  return Boolean(value?.job_id && value.validation_id && verifiedJobKey.value === jobBindingKey(value)
    && job && job.id === value.job_id && job.project_id === props.projectId
    && job.resource_ref.type === 'batch_validation' && job.resource_ref.id === value.validation_id
    && isTerminalJob(job.status))
})
const validationJobSuccessful = computed(() => validationJobTerminal.value && verifiedJob.value?.status === 'SUCCEEDED')
const terminalPredictionJob = computed(() => {
  const value = draft.value; const job = verifiedJob.value
  return Boolean(value?.job_id && verifiedJobKey.value === jobBindingKey(value) && job
    && job.id === value.job_id && job.project_id === props.projectId
    && job.resource_ref.type === 'batch_prediction' && job.resource_ref.id === value.id
    && job.status === value.execution_status && isTerminalJob(job.status))
})
const postProcessingLocked = computed(() => submissionLocked.value && !terminalPredictionJob.value)
const draftJobUnresolved = computed(() => Boolean(draft.value && (draft.value.job_id || draft.value.execution_status)
  && !validationJobTerminal.value))
const canEditDraft = computed(() => Boolean(props.canCreateBatch && readyTool.value && templateIsWritable.value && draft.value && !draftJobUnresolved.value
  && draft.value.draft_status !== 'VALIDATING' && !submissionLocked.value && !busy.value))
const canValidateDraft = computed(() => Boolean(props.canCreateBatch && props.canReadBatch && draft.value
  && !draftJobUnresolved.value && draft.value.draft_status !== 'VALIDATING' && artifactReady.value
  && readyTool.value && templateIsWritable.value && !submissionLocked.value && !busy.value && draft.value.template_version === requiredTemplateVersion.value))
const canUseValidation = computed(() => Boolean(draft.value && validation.value && validation.value.status === 'READY'
  && draft.value.template_version === requiredTemplateVersion.value
  && validation.value.template_version === draft.value.template_version && validation.value.draft_revision === draft.value.revision
  && validation.value.config_hash === draft.value.config_hash && validation.value.id === draft.value.validation_id
  && draft.value.draft_status === 'READY' && validationJobSuccessful.value
  && selectedModel.value && readyTool.value && exactModelMatchesValidation.value
  && !(draft.value.invalid_row_policy === 'block' && validation.value.invalid_rows > 0) && !validationError.value && !submissionLocked.value))
const exactModelMatchesValidation = computed(() => {
  const snapshot = validation.value?.model_snapshot
  const model = selectedModel.value
  return Boolean(snapshot && model && snapshot.id === model.id && snapshot.version === model.version
    && snapshot.input_schema_version === model.input_schema_version
    && snapshot.output_schema_version === model.output_schema_version
    && (!model.artifact_sha256 || snapshot.artifact_sha256 === model.artifact_sha256))
})
const resourceRef = computed<ResourceRef | null>(() => draft.value ? { type: 'batch_prediction', id: draft.value.id } : null)
const submitPreview = computed(() => confirmations.preview)
const submitPreviewMatches = computed(() => Boolean(resourceRef.value && draft.value && validation.value && submitPreview.value
  && confirmations.previewIsFor(resourceRef.value, 'batch.submit')
  && submitPreview.value.resource_ref.type === 'batch_prediction' && submitPreview.value.resource_ref.id === draft.value.id
  && submitPreview.value.action === 'batch.submit' && submitPreview.value.can_confirm
  && submitPreview.value.revision === draft.value.revision && submitPreview.value.etag === draft.value.etag
  && isSha256(submitPreview.value.config_hash) && confirmationHash.value === submitPreview.value.config_hash && canUseValidation.value))
const submitConfirmed = computed(() => Boolean(submitPreviewMatches.value && draft.value && confirmations.confirmation
  && confirmations.isFor(resourceRef.value!, 'batch.submit')
  && confirmations.confirmation.resource_ref.type === 'batch_prediction'
  && confirmations.confirmation.resource_ref.id === draft.value.id
  && confirmations.confirmation.project_id === props.projectId
  && confirmations.confirmation.action === 'batch.submit'
  && confirmations.confirmation.revision === draft.value.revision
  && confirmations.confirmation.config_hash === confirmationHash.value
  && confirmations.confirmation.policy_version === submitPreview.value?.policy_version
  && confirmations.confirmation.status === 'CONFIRMED'))
const canSubmit = computed(() => Boolean(props.canCreateBatch && submitConfirmed.value && !busy.value && !submissionLocked.value))
const canCancel = computed(() => Boolean(props.canReadBatch && props.canCancelBatch && draft.value?.job_id
  && verifiedJobKey.value === jobBindingKey(draft.value) && verifiedJob.value?.cancelable
  && ['QUEUED', 'RUNNING'].includes(verifiedJob.value.status)
  && verifiedJob.value.status === draft.value.execution_status && !busy.value))
const failedRows = computed(() => resultRows.value.filter((row) => row.status === 'FAILED'))
const canCreateRetryPlan = computed(() => Boolean(props.canCreateBatch && readyTool.value && templateIsWritable.value && failedRows.value.length
  && draft.value?.template_version === requiredTemplateVersion.value
  && terminalPredictionJob.value && selectedRetryRows.value.length && !busy.value && !postProcessingLocked.value && draft.value?.etag))
const retryResourceRef = computed<ResourceRef | null>(() => retryPlan.value ? { type: 'batch_retry_plan', id: retryPlan.value.id } : null)
const retryPreview = computed(() => confirmations.preview)
const retryPreviewMatches = computed(() => Boolean(retryPlan.value && retryResourceRef.value && retryPreview.value
  && confirmations.previewIsFor(retryResourceRef.value, 'batch.retry_failed')
  && retryPreview.value.action === 'batch.retry_failed' && retryPreview.value.resource_ref.type === 'batch_retry_plan'
  && retryPreview.value.resource_ref.id === retryPlan.value.id && retryPreview.value.can_confirm
  && retryPreview.value.revision === retryPlan.value.revision && retryPreview.value.etag === retryPlan.value.etag
  && isSha256(retryPreview.value.config_hash) && retryPreview.value.config_hash === retryPlan.value.config_hash
  && retryConfirmationHash.value === retryPreview.value.config_hash))
const retryConfirmed = computed(() => Boolean(retryPreviewMatches.value && retryPlan.value && confirmations.confirmation
  && confirmations.isFor(retryResourceRef.value!, 'batch.retry_failed')
  && confirmations.confirmation.resource_ref.type === 'batch_retry_plan'
  && confirmations.confirmation.resource_ref.id === retryPlan.value.id
  && confirmations.confirmation.action === 'batch.retry_failed'
  && confirmations.confirmation.revision === retryPlan.value.revision
  && confirmations.confirmation.config_hash === retryConfirmationHash.value
  && confirmations.confirmation.policy_version === retryPreview.value?.policy_version
  && confirmations.confirmation.status === 'CONFIRMED'))
const canRetryFailed = computed(() => Boolean(props.canCreateBatch && retryConfirmed.value && draft.value?.etag
  && terminalPredictionJob.value && !busy.value && !postProcessingLocked.value))
const canStartNewBatch = computed(() => Boolean(!busy.value && !postProcessingLocked.value
  && draft.value?.draft_status !== 'VALIDATING'
  && (!draft.value?.job_id || ['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'].includes(draft.value.execution_status ?? ''))))
const validationMessage = computed(() => {
  if (validation.value?.status === 'INVALID' && draft.value?.invalid_row_policy === 'block') return '校验发现无效行，阻止策略已停止提交。'
  if (validation.value?.status === 'INVALID') return '批量校验未通过；未执行预测。请修正文件并创建新草稿。'
  if (validation.value?.status === 'FAILED') return '服务端批量校验失败，未执行预测。'
  if (validation.value?.status === 'STALE') return '草稿版本已变化，当前校验结果已失效。'
  if (validation.value && ['QUEUED', 'RUNNING'].includes(validation.value.status)) return '服务端正在解析并校验批量文件。'
  if (validation.value?.status === 'READY' && draft.value?.invalid_row_policy === 'exclude_invalid' && validation.value.invalid_rows > 0) return `提交将排除 ${validation.value.excluded_rows} 行；这些行会标记为 SKIPPED，不会伪装成执行失败。`
  return ''
})
const selectedRetryRowsSet = computed(() => new Set(selectedRetryRows.value))
const exportInclude: BatchExportCreate['include'] = ['input_snapshot', 'result', 'model_version', 'warnings']

function modelKey(model: BatchModelOption) {
  return [model.id, model.version, model.input_schema_version, model.preprocessing_profile_id ?? '', model.preprocessing_profile_sha256 ?? ''].join('\u0000')
}
function isBatchTool(tool: BatchToolCapability): tool is BatchCapableTool {
  return 'chromatography_type' in tool && tool.id === 'batch' && tool.chromatography_type === props.chromatographyType
}
function compatibleModel(tool: BatchCapableTool | null | undefined, model: BatchModelOption | null | undefined): CompatibleModel | null {
  if (!tool || !model) return null
  const matches = tool.model_constraints.compatible_models.filter((item) => item.model_id === model.id
    && item.model_version === model.version && item.input_schema_version === model.input_schema_version
    && sameProfile(item, model))
  return matches.length === 1 ? matches[0] : null
}
function sameProfile(compatible: CompatibleModel, model: BatchModelOption) {
  if (!('tools' in compatible)) return !model.preprocessing_profile_id && !model.preprocessing_profile_sha256
  return sameProfileBinding(compatible, model)
}
function sameProfileBinding(compatible: ProfileBinding, model: BatchModelOption) {
  return (compatible.preprocessing_profile_id ?? null) === (model.preprocessing_profile_id ?? null)
    && (compatible.preprocessing_profile_sha256 ?? null) === (model.preprocessing_profile_sha256 ?? null)
}
function isSha256(value: unknown): value is string { return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value) }
function hasBudget(value: BatchResourceBudget | null | undefined): value is BatchResourceBudget {
  return Boolean(value && Number.isFinite(value.max_evaluations) && value.max_evaluations > 0
    && Number.isFinite(value.max_runtime_seconds) && value.max_runtime_seconds > 0)
}
function predictionLimitsReady(value: BatchTypeCapability['limits'] | null | undefined) {
  return Boolean(value && [value.max_rows, value.max_candidates, value.max_evaluations].every((item) => Number.isFinite(item) && item > 0))
}
function isCurrent(context: number) { return context === generation.value && props.canReadBatch && Boolean(client.value) }
function isCurrentOperation(epoch: number, context: number, selectedKey = selectedModelKey.value) {
  return isCurrent(context) && epoch === operationEpoch.value && selectedModelKey.value === selectedKey
}
function savedWorkspace() { return workspaceKey.value ? readBatchWorkspace(workspaceKey.value) : null }
function persist() {
  if (!workspaceKey.value) return
  const formDraft: FormDraft = { selectedModelKey: selectedModelKey.value, name: name.value,
    invalidRowPolicy: invalidRowPolicy.value, budget: { max_evaluations: maxEvaluations.value, max_runtime_seconds: maxRuntimeSeconds.value },
    templateFormat: templateFormat.value }
  emit('update:formDraft', formDraft)
  const prior = savedWorkspace()
  writeBatchWorkspace(workspaceKey.value, {
    version: 1, actorId: props.actorId, projectId: props.projectId, type: props.chromatographyType, tool: 'batch',
    formDraft, draftId: (draft.value?.id ?? prior?.draftId ?? recoverableDraftId.value) || undefined,
    artifactId: artifact.value?.id,
    submissionLocked: submissionLocked.value,
  })
}
function clearOperationState() {
  downloadingTemplate.value = false; uploading.value = false; refreshingArtifact.value = false; creatingDraft.value = false
  patchingDraft.value = false; validating.value = false; refreshingValidation.value = false; restoring.value = false
  loadingConfirmation.value = false; creatingConfirmation.value = false; submitting.value = false; canceling.value = false
  refreshingResults.value = false; creatingRetryPlan.value = false; loadingRetryConfirmation.value = false
  retryingFailed.value = false; creatingExport.value = false; refreshingExport.value = false; downloadingExport.value = false
}
function clearArtifact() { accepted.value = null; artifact.value = null }
function clearDraftState() {
  draft.value = null; validation.value = null; validationRows.value = []; resultRows.value = []; retryPlan.value = null
  selectedRetryRows.value = []; exportView.value = null; activeJobId.value = ''; validationError.value = ''
  verifiedJobKey.value = ''; verifiedJob.value = null
  confirmationHash.value = ''; retryConfirmationHash.value = ''; confirmations.reset()
}
function initializeBudget() {
  if (!toolLimits.value) return
  maxEvaluations.value = toolLimits.value.max_evaluations
  maxRuntimeSeconds.value = toolLimits.value.max_runtime_seconds
}
function modelSelection(model: BatchModelOption) {
  return {
    mode: 'exact' as const, model_id: model.id, model_version: model.version,
    ...(model.preprocessing_profile_id && model.preprocessing_profile_sha256
      ? { preprocessing_profile_id: model.preprocessing_profile_id, preprocessing_profile_sha256: model.preprocessing_profile_sha256 } : {}),
  }
}
function buildDraftBody(model: BatchModelOption, template: BatchTemplateDefinition, artifactId: string): BatchDraftCreate {
  const base = {
    project_id: props.projectId, name: name.value.trim() || `${props.chromatographyType.toUpperCase()} 批量预测`,
    schema_version: model.input_schema_version, model_selection: modelSelection(model), input_artifact_id: artifactId,
    template_version: template.version, field_mapping: Object.fromEntries(template.fields.map((field) => [field, field])),
    invalid_row_policy: invalidRowPolicy.value, resource_budget: { max_evaluations: maxEvaluations.value, max_runtime_seconds: maxRuntimeSeconds.value },
    tool: 'batch' as const,
  }
  switch (props.chromatographyType) {
    case 'tlc': return { ...base, chromatography_type: 'tlc', common_input: {}, }
    case 'cc': return { ...base, chromatography_type: 'cc', common_input: {}, }
    case 'hplc': return { ...base, chromatography_type: 'hplc', common_input: {}, }
    case 'gc': return { ...base, chromatography_type: 'gc', common_input: {}, }
    case 'sfc': return { ...base, chromatography_type: 'sfc', common_input: {}, }
  }
}
function draftMatchesSelection(value: BatchDraft) {
  const model = selectedModel.value
  return Boolean(model && value.id && value.project_id === props.projectId && value.chromatography_type === props.chromatographyType
    && value.tool === 'batch' && value.model_selection.mode === 'exact' && value.model_selection.model_id === model.id
    && value.model_selection.model_version === model.version && value.schema_version === model.input_schema_version
    && sameProfileBinding(value.model_selection, model)
    && value.template_version === selectedTemplate.value?.version && isSha256(value.config_hash)
    && Number.isInteger(value.revision) && value.revision > 0 && Boolean(value.etag))
}
function sameFieldMapping(actual: Record<string, string>, expected: Record<string, string>) {
  const keys = Object.keys(expected)
  return Object.keys(actual).length === keys.length && keys.every((key) => actual[key] === expected[key])
}
function applyDraft(value: BatchDraft) {
  if (verifiedJobKey.value !== jobBindingKey(value)) { verifiedJobKey.value = ''; verifiedJob.value = null }
  draft.value = value
  invalidRowPolicy.value = value.invalid_row_policy
  maxEvaluations.value = value.resource_budget.max_evaluations
  maxRuntimeSeconds.value = value.resource_budget.max_runtime_seconds
  persist()
}
async function refreshBoundJob(epoch: number, context: number, identity: string) {
  const value = draft.value
  if (!value?.job_id) { verifiedJobKey.value = ''; verifiedJob.value = null; return false }
  const key = jobBindingKey(value)
  verifiedJobKey.value = ''; verifiedJob.value = null
  submissionLocked.value = true
  try {
    await jobs.fetchJob(value.job_id)
    if (!isCurrentOperation(epoch, context, identity) || jobBindingKey(draft.value) !== key) return false
    const job = jobs.jobs[value.job_id]
    if (!job || job.id !== value.job_id || job.project_id !== props.projectId) return false
    const validationBound = job.resource_ref.type === 'batch_validation' && job.resource_ref.id === value.validation_id
    const predictionBound = job.resource_ref.type === 'batch_prediction' && job.resource_ref.id === value.id
    if (!validationBound && !predictionBound) return false
    verifiedJob.value = job
    verifiedJobKey.value = key
    if (!validationBound || !isTerminalJob(job.status)) return false
    submissionLocked.value = false
    return true
  } catch {
    if (isCurrentOperation(epoch, context, identity) && jobBindingKey(draft.value) === key) submissionLocked.value = true
    return false
  }
}
function invalidateValidation() {
  validation.value = null; validationRows.value = []; resultRows.value = []; retryPlan.value = null
  selectedRetryRows.value = []; validationError.value = ''; activeJobId.value = ''; confirmationHash.value = ''
  retryConfirmationHash.value = ''; confirmations.reset()
}
function selectedModelChanged() {
  const current = ++toolGeneration.value
  const context = generation.value
  const selectedKey = selectedModelKey.value
  operationEpoch.value++
  confirmations.reset(); confirmationHash.value = ''; retryConfirmationHash.value = ''
  toolCapability.value = null; toolCapabilityModelKey.value = ''; downloadedTemplateVersion.value = ''
  clearArtifact(); clearDraftState(); recoverableDraftId.value = savedWorkspace()?.draftId ?? ''
  error.value = ''; clearOperationState()
  const model = selectedModel.value
  const advertised = advertisedBatchTool.value
  if (!model || model.health.status !== 'READY' || matchingModels.value.length !== 1 || !capabilityReady.value || !client.value) return
  if (!advertised) { error.value = '服务端没有当前模型版本、schema 与预处理 profile 的唯一批量兼容能力。'; return }
  void client.value.getToolCapability(advertised.schema_version).then((value) => {
    if (!isCurrent(context) || current !== toolGeneration.value || selectedKey !== selectedModelKey.value) return
    if (!('chromatography_type' in value) || value.id !== 'batch' || value.chromatography_type !== props.chromatographyType) throw new Error('服务端批量 schema 与当前类型不匹配。')
    toolCapability.value = value
    toolCapabilityModelKey.value = selectedKey
    const compatible = compatibleModel(value, model)
    if (value.schema_version !== advertised.schema_version || !compatible) error.value = '服务端工具 schema 与当前模型、profile 或兼容能力不匹配。'
    else if (!readyTool.value) error.value = value.health.reason || '当前模型、批量模板或服务尚未就绪。'
    else initializeBudget()
  }).catch((reason) => {
    if (isCurrent(context) && current === toolGeneration.value) error.value = reason instanceof Error ? reason.message : '批量 schema 读取失败'
  })
}
async function loadCapabilities() {
  const context = ++generation.value
  operationEpoch.value++
  toolGeneration.value++
  confirmations.reset()
  capability.value = null; toolCapability.value = null; toolCapabilityModelKey.value = ''; selectedModelKey.value = ''
  clearDraftState(); clearArtifact(); recoverableDraftId.value = ''; downloadedTemplateVersion.value = ''
  submissionLocked.value = false; clearOperationState(); error.value = ''; loading.value = false
  if (!props.actorId || !props.projectId || !props.canReadBatch || !client.value) return
  const saved = savedWorkspace()
  recoverableDraftId.value = saved?.draftId ?? ''
  submissionLocked.value = props.initialBatchId
    ? saved?.draftId === props.initialBatchId && (saved.submissionLocked ?? false)
    : saved?.submissionLocked ?? false
  if (saved?.formDraft && typeof saved.formDraft === 'object') {
    const form = saved.formDraft as Partial<FormDraft>
    if (typeof form.selectedModelKey === 'string') selectedModelKey.value = form.selectedModelKey
    if (typeof form.name === 'string') name.value = form.name
    if (form.invalidRowPolicy === 'block' || form.invalidRowPolicy === 'exclude_invalid') invalidRowPolicy.value = form.invalidRowPolicy
    if (form.templateFormat === 'csv' || form.templateFormat === 'xlsx') templateFormat.value = form.templateFormat
  }
  loading.value = true
  try {
    const result = await client.value.getTypeCapability()
    if (!isCurrent(context)) return
    capability.value = result
    if (result.id !== props.chromatographyType) throw new Error('服务端返回的能力与当前色谱类型不匹配。')
  } catch (reason) {
    if (isCurrent(context)) error.value = reason instanceof Error ? reason.message : '批量能力读取失败'
  } finally {
    if (isCurrent(context)) {
      loading.value = false
      if (capability.value?.id === props.chromatographyType && props.initialBatchId) void restoreBatchById(props.initialBatchId)
      else if (capability.value?.id === props.chromatographyType && selectedModelKey.value) selectedModelChanged()
    }
  }
}
async function downloadTemplate() {
  const model = selectedModel.value
  const template = selectedTemplate.value
  if (!model || !template || !canDownloadTemplate.value || !client.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const modelIdentity = modelKey(model)
  downloadingTemplate.value = true; downloadedTemplateVersion.value = ''; error.value = ''
  try {
    const file = await client.value.downloadTemplate(model.id, templateFormat.value, template.version)
    if (!isCurrentOperation(epoch, context, modelIdentity) || file.version !== template.version || !readyTool.value) return
    const url = URL.createObjectURL(file.blob)
    const anchor = document.createElement('a'); anchor.href = url
    anchor.download = `${props.chromatographyType}-batch.${templateFormat.value}`; anchor.click(); URL.revokeObjectURL(url)
    downloadedTemplateVersion.value = file.version
  } catch (reason) {
    if (isCurrentOperation(epoch, context, modelIdentity)) error.value = reason instanceof Error ? reason.message : '批量模板下载失败'
  } finally {
    if (isCurrentOperation(epoch, context, modelIdentity)) downloadingTemplate.value = false
  }
}
function selectFile(event: Event) {
  if (busy.value) return
  const input = event.target as HTMLInputElement
  operationEpoch.value++
  clearDraftState(); clearArtifact(); selectedFile.value = input.files?.[0] ?? null
  submissionLocked.value = false; error.value = ''; persist()
}
function startNewBatch() {
  if (!canStartNewBatch.value) return
  operationEpoch.value++
  clearDraftState(); clearArtifact(); selectedFile.value = null; recoverableDraftId.value = ''
  submissionLocked.value = false; invalidRowPolicy.value = 'block'; initializeBudget(); error.value = ''
  if (workspaceKey.value) writeBatchWorkspace(workspaceKey.value, null)
  persist()
  if (fileInput.value) fileInput.value.value = ''
}
async function uploadFile() {
  const file = selectedFile.value
  if (!file || !props.canUploadBatch || !canSelectFile.value || !client.value) return
  const modelIdentity = modelKey(selectedModel.value!)
  const epoch = ++operationEpoch.value; const context = generation.value
  uploading.value = true; error.value = ''; clearArtifact()
  try {
    const body: ContractCreateUploadBody = { project_id: props.projectId, kind: 'batch_input', file_name: file.name,
      media_type: file.type || (file.name.toLowerCase().endsWith('.xlsx') ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' : 'text/csv'),
      size_bytes: file.size }
    const upload = await artifacts.createUpload(body)
    if (!isCurrentOperation(epoch, context, modelIdentity) || !props.canUploadBatch) return
    await artifacts.uploadContent(upload.data.id, file)
    if (!isCurrentOperation(epoch, context, modelIdentity)) return
    const result = await artifacts.completeUpload(upload.data.id)
    if (!isCurrentOperation(epoch, context, modelIdentity)) return
    if (result.data.resource_ref.type !== 'artifact' || result.data.artifact_id !== result.data.resource_ref.id) throw new Error('服务端制品受理响应绑定不一致。')
    accepted.value = result.data
    await refreshArtifact(result.data.artifact_id, epoch, context, modelIdentity)
    persist()
  } catch (reason) {
    if (isCurrentOperation(epoch, context, modelIdentity)) error.value = reason instanceof Error ? reason.message : '批量文件上传失败'
  } finally {
    if (isCurrentOperation(epoch, context, modelIdentity)) uploading.value = false
  }
}
async function refreshArtifact(artifactId = accepted.value?.artifact_id, epoch = operationEpoch.value, context = generation.value, modelIdentity = selectedModelKey.value) {
  if (!artifactId || !props.canReadBatch) return
  refreshingArtifact.value = true
  try {
    const value = await artifacts.getArtifact(artifactId)
    if (!isCurrentOperation(epoch, context, modelIdentity)) return
    if (value.id !== artifactId || value.project_id !== props.projectId || value.kind !== 'batch_input') throw new Error('服务端制品与当前项目或批量输入类型不匹配。')
    artifact.value = value; persist()
  } catch (reason) {
    if (isCurrentOperation(epoch, context, modelIdentity)) error.value = reason instanceof Error ? reason.message : '批量文件状态读取失败'
  } finally {
    if (isCurrentOperation(epoch, context, modelIdentity)) refreshingArtifact.value = false
  }
}
function draftMatchesCurrent(value: BatchDraft) {
  const model = selectedModel.value
  const exact = value.model_selection.mode === 'exact' ? value.model_selection : null
  const profileMatches = !model || Boolean(exact
    && (exact.preprocessing_profile_id ?? null) === (model.preprocessing_profile_id ?? null)
    && (exact.preprocessing_profile_sha256 ?? null) === (model.preprocessing_profile_sha256 ?? null))
  return Boolean(value.project_id === props.projectId && value.chromatography_type === props.chromatographyType
    && value.tool === 'batch' && value.schema_version
    && (!model || Boolean(exact && exact.model_id === model.id && exact.model_version === model.version
      && value.schema_version === model.input_schema_version))
    && profileMatches && Boolean(value.template_version) && isSha256(value.config_hash)
    && value.revision > 0 && value.etag)
}
async function createDraft() {
  const model = selectedModel.value; const template = selectedTemplate.value; const acceptedArtifact = accepted.value
  if (!model || !template || !acceptedArtifact || !artifactReady.value || !canCreateDraft.value || !client.value) return
  const expectedMap = Object.fromEntries(template.fields.map((field) => [field, field]))
  const body = buildDraftBody(model, template, acceptedArtifact.artifact_id)
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = modelKey(model)
  creatingDraft.value = true; error.value = ''; confirmations.reset()
  try {
    const result = await client.value.createDraft(body)
    if (!isCurrentOperation(epoch, context, identity) || !props.canCreateBatch) return
    if (!draftMatchesCurrent(result.data) || result.data.template_version !== template.version || result.data.input_artifact_id !== acceptedArtifact.artifact_id
      || !sameFieldMapping(result.data.field_mapping, expectedMap)) throw new Error('服务端草稿与当前项目、模型、模板或上传文件绑定不一致。')
    draft.value = result.data; recoverableDraftId.value = result.data.id; applyDraft(result.data); persist()
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '批量草稿创建失败'
  } finally {
    if (isCurrentOperation(epoch, context, identity)) creatingDraft.value = false
  }
}
async function patchDraft(patch: { invalid_row_policy?: 'block' | 'exclude_invalid'; resource_budget?: BatchResourceBudget }) {
  const currentDraft = draft.value
  if (!currentDraft || !canEditDraft.value || !client.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  invalidateValidation(); patchingDraft.value = true; error.value = ''
  try {
    const result = await client.value.patchDraft(currentDraft.id, patch, currentDraft.etag)
    if (!isCurrentOperation(epoch, context, identity) || !props.canCreateBatch) return
    if (!draftMatchesCurrent(result.data) || result.data.id !== currentDraft.id) throw new Error('更新后的草稿与当前模型或模板不匹配。')
    applyDraft(result.data)
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) { applyDraft(currentDraft); error.value = reason instanceof Error ? reason.message : '批量草稿更新失败' }
  } finally {
    if (isCurrentOperation(epoch, context, identity)) patchingDraft.value = false
  }
}
function changePolicy() { if (draft.value) void patchDraft({ invalid_row_policy: invalidRowPolicy.value }); else persist() }
function changeBudget() {
  if (!validBudget.value) {
    if (draft.value) { maxEvaluations.value = draft.value.resource_budget.max_evaluations; maxRuntimeSeconds.value = draft.value.resource_budget.max_runtime_seconds }
    return
  }
  if (draft.value) void patchDraft({ resource_budget: { max_evaluations: maxEvaluations.value, max_runtime_seconds: maxRuntimeSeconds.value } })
  else persist()
}
function changeName() { if (!draft.value) persist() }
function changeFormat() { downloadedTemplateVersion.value = ''; persist() }
async function validateDraft() {
  const currentDraft = draft.value
  if (!currentDraft || !canValidateDraft.value || !draftMatchesCurrent(currentDraft) || !client.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  invalidateValidation(); validating.value = true; error.value = ''
  try {
    const result = await client.value.validate(currentDraft.id, currentDraft.etag)
    if (!isCurrentOperation(epoch, context, identity) || !props.canCreateBatch) return
    activeJobId.value = result.data.job_id
    await refreshValidation(epoch, context, identity)
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '批量校验提交失败'
  } finally {
    if (isCurrentOperation(epoch, context, identity)) validating.value = false
  }
}
function resolvedInputMatches(row: RowValidation, currentValidation: BatchValidation, currentDraft: BatchDraft): row is RowValidation & { resolved_input_snapshot: BatchResolvedInput } {
  const value = row.resolved_input_snapshot
  if (!value) return row.status === 'INVALID'
  const type = value.chromatography_type ?? 'tlc'
  return type === props.chromatographyType && value.project_id === props.projectId
    && value.draft_revision === currentDraft.revision && value.validation_id === currentValidation.id
    && value.template_version === currentDraft.template_version
}
async function refreshValidation(epoch = ++operationEpoch.value, context = generation.value, identity = selectedModelKey.value) {
  const currentDraft = draft.value
  if (!currentDraft || !props.canReadBatch || !client.value) return
  refreshingValidation.value = true; validationError.value = ''; confirmations.reset(); confirmationHash.value = ''
  try {
    const [draftResult, currentValidation] = await Promise.all([
      client.value.getBatch(currentDraft.id), client.value.getValidation(currentDraft.id),
    ])
    if (!isCurrentOperation(epoch, context, identity)) return
    if (!draftMatchesCurrent(draftResult.data) || draftResult.data.id !== currentDraft.id) throw new Error('当前草稿与项目、模型版本或模板不匹配。')
    applyDraft(draftResult.data)
    const jobBound = !draftResult.data.job_id || await refreshBoundJob(epoch, context, identity)
    if (!isCurrentOperation(epoch, context, identity)) return
    if (!jobBound) {
      validationError.value = '批量校验任务尚未能与当前项目、validation 绑定并确认终态。'
    }
    if (currentValidation.draft_revision !== draftResult.data.revision || currentValidation.config_hash !== draftResult.data.config_hash
      || currentValidation.id !== draftResult.data.validation_id || currentValidation.template_version !== draftResult.data.template_version) {
      invalidateValidation(); throw new Error('服务端草稿与行校验版本不一致，已停止预览与确认。')
    }
    validation.value = currentValidation
    if (draftResult.data.job_id) activeJobId.value = draftResult.data.job_id
    if (['READY', 'INVALID'].includes(currentValidation.status)) {
      const rows: RowValidation[] = []; let cursor: string | undefined
      do {
        const page = await client.value.listValidationRows(currentDraft.id, { cursor, limit: 200 })
        if (!isCurrentOperation(epoch, context, identity)) return
        rows.push(...page.rows); cursor = page.has_more ? page.next_cursor ?? undefined : undefined
        if (page.has_more && !cursor) throw new Error('服务端校验行分页游标缺失。')
      } while (cursor)
      validationRows.value = rows
      if (rows.length !== currentValidation.total_rows || rows.some((row) => !resolvedInputMatches(row, currentValidation, draftResult.data))) {
        validationError.value = '部分行缺少与当前草稿和校验绑定的 typed 输入快照，已停止确认与提交。'
      } else if (props.chromatographyType === 'sfc' && rows.some((row) => row.resolved_input_snapshot?.chromatography_type === 'sfc'
        && row.resolved_input_snapshot.template_version === 'sfc-batch-2'
        && row.resolved_input_snapshot.request.tool !== 'single_column' && row.resolved_input_snapshot.request.tool !== 'full_column')) {
        validationError.value = 'SFC 行缺少明确的 single/full tool 快照，已停止确认与提交。'
      } else if (props.chromatographyType === 'sfc') {
        const supportedTools = selectedCompatibility.value && 'tools' in selectedCompatibility.value
          ? selectedCompatibility.value.tools : []
        const unsupportedRows = rows.filter((row) => row.status !== 'INVALID'
          && row.resolved_input_snapshot?.chromatography_type === 'sfc'
          && !supportedTools.includes(row.resolved_input_snapshot.request.tool)).map((row) => row.source_row_number)
        if (unsupportedRows.length) validationError.value = `SFC 第 ${unsupportedRows.join('、')} 行的工具模式不在当前 exact 模型兼容范围内，已停止确认与提交。`
      }
    } else validationRows.value = []
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) validationError.value = reason instanceof Error ? reason.message : '批量校验结果读取失败'
  } finally {
    if (isCurrentOperation(epoch, context, identity)) refreshingValidation.value = false
  }
}
async function restoreBatchById(id: string) {
  if (!id || !props.canReadBatch || busy.value || !client.value) return
  const context = generation.value
  error.value = ''; restoring.value = true
  try {
    const result = await client.value.getBatch(id)
    if (!isCurrent(context)) return
    const value = result.data
    if (value.id !== id || value.project_id !== props.projectId || value.chromatography_type !== props.chromatographyType || value.tool !== 'batch') {
      throw new Error('批次记录与当前 id、项目、色谱类型或工具不匹配。')
    }
    const exact = value.model_selection.mode === 'exact' ? value.model_selection : null
    const matchingModels = exact ? models.value.filter((model) => model.id === exact.model_id && model.version === exact.model_version
      && model.input_schema_version === value.schema_version
      && (model.preprocessing_profile_id ?? null) === (exact.preprocessing_profile_id ?? null)
      && (model.preprocessing_profile_sha256 ?? null) === (exact.preprocessing_profile_sha256 ?? null)) : []
    const nextModelKey = matchingModels.length === 1 ? modelKey(matchingModels[0]) : ''
    const modelUnchanged = selectedModelKey.value === nextModelKey
    selectedModelKey.value = nextModelKey
    await nextTick()
    if (!isCurrent(context)) return
    if (modelUnchanged && nextModelKey) selectedModelChanged()
    restoring.value = true
    recoverableDraftId.value = id
    draft.value = result.data; applyDraft(result.data)
    const inputArtifact = await artifacts.getArtifact(result.data.input_artifact_id)
    if (!isCurrent(context)) return
    if (inputArtifact.project_id !== props.projectId || inputArtifact.kind !== 'batch_input' || inputArtifact.id !== result.data.input_artifact_id) throw new Error('草稿引用的输入制品不属于当前项目。')
    artifact.value = inputArtifact
    accepted.value = null
    const identity = selectedModelKey.value
    if (result.data.validation_id) await refreshValidation(operationEpoch.value, context, identity)
    if (result.data.job_id) await refreshResults(operationEpoch.value, context, identity)
    persist()
  } catch (reason) {
    if (isCurrent(context)) error.value = reason instanceof Error ? reason.message : '读取批量草稿失败'
  } finally {
    if (isCurrent(context)) restoring.value = false
  }
}
async function restoreDraft() { await restoreBatchById(recoverableDraftId.value) }
async function refreshArtifactState() {
  const id = accepted.value?.artifact_id ?? artifact.value?.id
  if (!id || !props.canReadBatch) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  await refreshArtifact(id, epoch, context, identity)
}
async function loadSubmitPreview() {
  if (!resourceRef.value || !draft.value || !canUseValidation.value || !props.canCreateBatch || busy.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  confirmations.reset(); confirmationHash.value = ''; loadingConfirmation.value = true; error.value = ''
  try {
    const preview = await confirmations.loadPreview(resourceRef.value, 'batch.submit')
    if (!isCurrentOperation(epoch, context, identity)) return
    if (preview.resource_ref.type !== 'batch_prediction' || preview.resource_ref.id !== draft.value?.id
      || preview.action !== 'batch.submit' || preview.revision !== draft.value.revision || preview.etag !== draft.value.etag
      || !isSha256(preview.config_hash) || !canUseValidation.value) throw new Error('确认预览未绑定当前 revision、ETag、validation 与解析快照。')
    confirmationHash.value = preview.config_hash
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '读取提交确认摘要失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) loadingConfirmation.value = false }
}
async function confirmSubmit() {
  if (!submitPreviewMatches.value || !props.canCreateBatch || busy.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  creatingConfirmation.value = true; error.value = ''
  try {
    await confirmations.create()
    if (!isCurrentOperation(epoch, context, identity) || !submitConfirmed.value) throw new Error('确认记录与当前草稿摘要不匹配。')
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '创建提交确认失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) creatingConfirmation.value = false }
}
async function submitDraft() {
  const currentDraft = draft.value; const confirmation = confirmations.confirmation
  if (!currentDraft || !confirmation || !canSubmit.value || !client.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value; const scope = workspaceKey.value
  let postAccepted = false
  submissionLocked.value = true; persist()
  submitting.value = true; error.value = ''
  try {
    const result = await client.value.submit(currentDraft.id, confirmation.id)
    if (!isCurrentOperation(epoch, context, identity) || workspaceKey.value !== scope || !props.canCreateBatch) return
    if (result.data.id !== currentDraft.id || result.data.resource_ref.type !== 'batch_prediction' || result.data.resource_ref.id !== currentDraft.id) throw new Error('提交受理结果与当前批量草稿不一致。')
    postAccepted = true
    verifiedJobKey.value = ''; verifiedJob.value = null
    activeJobId.value = result.data.job_id
    const latest = await client.value.getBatch(currentDraft.id)
    if (!isCurrentOperation(epoch, context, identity) || workspaceKey.value !== scope) return
    if (!draftMatchesCurrent(latest.data) || latest.data.id !== currentDraft.id) throw new Error('提交后的服务端草稿绑定发生变化。')
    draft.value = latest.data; applyDraft(latest.data); resultRows.value = []; submissionLocked.value = true; confirmations.reset(); persist()
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity) && workspaceKey.value === scope) {
      const rejected = !postAccepted && reason instanceof ApiClientError && reason.status >= 400 && reason.status < 500 && reason.status !== 408
      submissionLocked.value = !rejected; persist()
      error.value = rejected
        ? `${reason.message}；服务端已拒绝本次提交，可修正后重新确认。`
        : `${reason instanceof Error ? reason.message : '提交结果暂不可确认'}；已锁定重复提交，请先读取服务端草稿状态。`
    }
  } finally { if (isCurrentOperation(epoch, context, identity) && workspaceKey.value === scope) submitting.value = false }
}
async function refreshResults(epoch = ++operationEpoch.value, context = generation.value, identity = selectedModelKey.value) {
  const currentDraft = draft.value
  if (!currentDraft || !props.canReadBatch || !client.value) return
  refreshingResults.value = true; error.value = ''
  try {
    const latest = await client.value.getBatch(currentDraft.id)
    if (!isCurrentOperation(epoch, context, identity)) return
    if (!draftMatchesCurrent(latest.data) || latest.data.id !== currentDraft.id) throw new Error('服务端草稿与当前项目、模型或模板不匹配。')
    const rows: BatchRowResult[] = []; let cursor: string | undefined
    do {
      const page = await client.value.listRows(currentDraft.id, { cursor, limit: 200 })
      if (!isCurrentOperation(epoch, context, identity)) return
      rows.push(...page.rows); cursor = page.has_more ? page.next_cursor ?? undefined : undefined
      if (page.has_more && !cursor) throw new Error('服务端结果分页游标缺失。')
    } while (cursor)
    draft.value = latest.data; applyDraft(latest.data); resultRows.value = rows; activeJobId.value = latest.data.job_id ?? ''
    if (latest.data.job_id) await refreshBoundJob(epoch, context, identity)
    else if (latest.data.execution_status) submissionLocked.value = true
    persist()
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '批量任务状态读取失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) refreshingResults.value = false }
}
async function cancelBatch() {
  const currentDraft = draft.value
  if (!currentDraft || !canCancel.value || !client.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  canceling.value = true; error.value = ''; confirmations.reset()
  try {
    const result = await client.value.cancel(currentDraft.id, currentDraft.etag, 'user_requested')
    if (!isCurrentOperation(epoch, context, identity) || !props.canCancelBatch) return
    if (!draftMatchesCurrent(result.data) || result.data.id !== currentDraft.id) throw new Error('取消响应与当前批量草稿不匹配。')
    draft.value = result.data; applyDraft(result.data); canceling.value = false; void refreshResults()
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '取消批量任务失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) canceling.value = false }
}
async function makeRetryPlan() {
  const currentDraft = draft.value
  if (!currentDraft || !canCreateRetryPlan.value || !client.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  creatingRetryPlan.value = true; error.value = ''; retryPlan.value = null; confirmations.reset()
  try {
    const result = await client.value.createRetryPlan(currentDraft.id, selectedRetryRows.value, currentDraft.etag)
    if (!isCurrentOperation(epoch, context, identity)) return
    const plan = result.data
    if (plan.parent_batch_id !== currentDraft.id || plan.expected_attempt !== currentDraft.attempt
      || plan.revision !== currentDraft.revision || plan.row_ids.length !== selectedRetryRows.value.length
      || plan.row_ids.some((id) => !selectedRetryRows.value.includes(id))) throw new Error('服务端重试计划与当前失败行、批次 attempt 或 revision 不匹配。')
    retryPlan.value = plan
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '创建失败行重试计划失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) creatingRetryPlan.value = false }
}
async function loadRetryPreview() {
  if (!retryResourceRef.value || !retryPlan.value || busy.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  confirmations.reset(); retryConfirmationHash.value = ''; loadingRetryConfirmation.value = true; error.value = ''
  try {
    const preview = await confirmations.loadPreview(retryResourceRef.value, 'batch.retry_failed')
    if (!isCurrentOperation(epoch, context, identity)) return
    if (preview.resource_ref.type !== 'batch_retry_plan' || preview.resource_ref.id !== retryPlan.value?.id
      || preview.action !== 'batch.retry_failed' || preview.revision !== retryPlan.value.revision
      || preview.etag !== retryPlan.value.etag || preview.config_hash !== retryPlan.value.config_hash || !isSha256(preview.config_hash)) {
      throw new Error('重试确认预览与当前失败行计划不匹配。')
    }
    retryConfirmationHash.value = preview.config_hash
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '读取重试确认摘要失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) loadingRetryConfirmation.value = false }
}
async function confirmRetry() {
  if (!retryPreviewMatches.value || !props.canCreateBatch || busy.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  creatingConfirmation.value = true; error.value = ''
  try {
    await confirmations.create()
    if (!isCurrentOperation(epoch, context, identity) || !retryConfirmed.value) throw new Error('重试确认记录与当前计划不匹配。')
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '创建重试确认失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) creatingConfirmation.value = false }
}
async function retryFailed() {
  const currentDraft = draft.value; const plan = retryPlan.value; const confirmation = confirmations.confirmation
  if (!currentDraft || !plan || !confirmation || !canRetryFailed.value || !client.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value; const scope = workspaceKey.value
  let postAccepted = false
  verifiedJobKey.value = ''; verifiedJob.value = null
  submissionLocked.value = true; persist()
  retryingFailed.value = true; error.value = ''
  try {
    const acceptedResult = await client.value.retryFailed(currentDraft.id, plan.id, confirmation.id, currentDraft.etag)
    if (!isCurrentOperation(epoch, context, identity) || workspaceKey.value !== scope || !props.canCreateBatch) return
    if (acceptedResult.data.id !== currentDraft.id || acceptedResult.data.resource_ref.type !== 'batch_prediction'
      || acceptedResult.data.resource_ref.id !== currentDraft.id) throw new Error('失败行重试受理结果与当前批次不匹配。')
    postAccepted = true
    activeJobId.value = acceptedResult.data.job_id; confirmations.reset(); retryPlan.value = null
    await refreshResults(epoch, context, identity)
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity) && workspaceKey.value === scope) {
      const rejected = !postAccepted && reason instanceof ApiClientError && reason.status >= 400 && reason.status < 500 && reason.status !== 408
      submissionLocked.value = !rejected; persist()
      error.value = rejected
        ? `${reason.message}；服务端已拒绝本次重试，可重新生成计划并确认。`
        : `${reason instanceof Error ? reason.message : '失败行重试结果暂不可确认'}；已锁定重复重试，请先读取服务端状态。`
    }
  } finally { if (isCurrentOperation(epoch, context, identity) && workspaceKey.value === scope) retryingFailed.value = false }
}
async function createExport() {
  const currentDraft = draft.value
  if (!currentDraft || !props.canReadBatch || !client.value || busy.value || postProcessingLocked.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  creatingExport.value = true; error.value = ''; exportView.value = null
  try {
    const body: BatchExportCreate = { format: exportFormat.value, include: exportInclude }
    const result = await client.value.createExport(currentDraft.id, body, currentDraft.etag)
    if (!isCurrentOperation(epoch, context, identity)) return
    if (result.data.resource_ref.type !== 'batch_export' || result.data.id !== result.data.resource_ref.id) throw new Error('批量导出受理响应绑定不一致。')
    const latest = await client.value.getExport(result.data.id)
    if (!isCurrentOperation(epoch, context, identity)) return
    if (latest.batch_id !== currentDraft.id) throw new Error('导出任务不属于当前批次。')
    exportView.value = latest
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '创建批量导出失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) creatingExport.value = false }
}
async function refreshExport() {
  if (!exportView.value || !client.value || !props.canReadBatch || busy.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  refreshingExport.value = true
  try {
    const latest = await client.value.getExport(exportView.value.id)
    if (!isCurrentOperation(epoch, context, identity)) return
    if (latest.batch_id !== draft.value?.id || latest.id !== exportView.value.id) throw new Error('导出状态与当前批次不匹配。')
    exportView.value = latest
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '读取导出状态失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) refreshingExport.value = false }
}
async function downloadExport() {
  const current = exportView.value
  if (!current || current.status !== 'SUCCEEDED' || !current.artifact_id || !props.canReadBatch || !client.value || busy.value) return
  const epoch = ++operationEpoch.value; const context = generation.value; const identity = selectedModelKey.value
  downloadingExport.value = true; error.value = ''
  try {
    const value = await artifacts.getArtifact(current.artifact_id)
    if (!isCurrentOperation(epoch, context, identity)) return
    if (value.id !== current.artifact_id || value.project_id !== props.projectId || value.kind !== 'export_report' || value.status !== 'AVAILABLE') {
      throw new Error('导出制品尚未通过当前项目和可用状态核验。')
    }
    const blob = await client.value.downloadExport(current.id)
    if (!isCurrentOperation(epoch, context, identity)) return
    const url = URL.createObjectURL(blob); const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `${props.chromatographyType}-batch-${draft.value?.id ?? 'export'}.${current.format}`; anchor.click(); URL.revokeObjectURL(url)
  } catch (reason) {
    if (isCurrentOperation(epoch, context, identity)) error.value = reason instanceof Error ? reason.message : '下载批量导出失败'
  } finally { if (isCurrentOperation(epoch, context, identity)) downloadingExport.value = false }
}
function snapshotLines(value: BatchResolvedInput): string[] {
  if ('normalized_smiles' in value) {
    return [`规范 SMILES：${value.normalized_smiles}`, `结构引用：${value.structure_ref}`, `展开剂条件：${formatResultCell(value.solvent_conditions)}`]
  }
  if (!('request' in value)) {
    return [`旧模板 TLC v1 · 结构引用：${value.structure_ref}`, `展开剂条件：${formatResultCell(value.solvent_conditions)}`]
  }
  if (value.chromatography_type === 'cc') return [`CC结构：${value.normalized_structures.join(' / ')}`, `CC条件：${formatResultCell(value.request.input)}`]
  if (value.chromatography_type === 'hplc') return [`原始有序 SMILES：${value.request.input.model_input_smiles.join(' / ')}`, `HPLC条件：${formatResultCell(value.request.input)}`]
  if (value.chromatography_type === 'gc') return [`规范结构：${value.normalized_structures.join(' / ')}`, `GC温程：${formatResultCell(value.request.input.temperature_program)}`]
  const mode = value.request.tool
  const column = value.request.tool === 'single_column' ? `单柱 ${value.request.input.column_id}` : '全柱范围'
  const provenance = value.scope_provenance
    ? `已观测 ${value.scope_provenance.observed_count} / 候选 ${value.scope_provenance.candidate_count}`
    : '候选来源未知'
  return [`SFC工具：${mode}`, `结构：${value.normalized_structures.join(' / ')}`, `候选范围：${column} · ${provenance}`, `条件：${formatResultCell(value.request.input)}`]
}
function previewLines(row: RowValidation) {
  const value = row.resolved_input_snapshot
  if (!value || !validation.value || !draft.value || !resolvedInputMatches(row, validation.value, draft.value)) return []
  return snapshotLines(value)
}
function resultCells(row: BatchRowResult): Array<{ key: string; value: string }> {
  const result = row.result
  if (!result || result.task_type !== props.chromatographyType || !Array.isArray(result.rows)) return []
  return result.rows.flatMap((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    return Object.entries(item).map(([key, value]) => ({ key: `${index + 1}.${key}`, value: formatResultCell(value, key) }))
  })
}
function toggleRow(row: string) {
  selectedRetryRows.value = selectedRetryRows.value.includes(row) ? selectedRetryRows.value.filter((id) => id !== row) : [...selectedRetryRows.value, row]
  retryPlan.value = null; retryConfirmationHash.value = ''; confirmations.reset()
}
function resultStatus(status: BatchRowResult['status']) {
  return ({ QUEUED: '排队中', RUNNING: '运行中', SUCCEEDED: '完成', FAILED: '失败', SKIPPED: '已跳过（未执行）', CANCELED: '已取消' } as const)[status]
}
function downloadTemplateCsvOnly() { return templateFormat.value === 'csv' ? '.csv,text/csv' : '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }

watch(() => [props.actorId, props.projectId, props.chromatographyType, props.initialBatchId, props.canReadBatch, props.canUploadBatch,
  props.canCreateBatch, props.canCancelBatch, props.permissionRevision, props.policyRevision], () => { void loadCapabilities() }, { immediate: true })
watch(selectedModelKey, selectedModelChanged)
watch([name, invalidRowPolicy, maxEvaluations, maxRuntimeSeconds, templateFormat], persist)
onBeforeUnmount(() => { generation.value++; toolGeneration.value++; operationEpoch.value++ })
</script>

<template>
  <section class="panel batch-workspace" :aria-labelledby="`batch-title-${chromatographyType}`">
    <div class="panel-heading"><div><span class="eyebrow">{{ chromatographyType.toUpperCase() }} · BATCH</span><h2 :id="`batch-title-${chromatographyType}`">{{ chromatographyType.toUpperCase() }} 批量预测</h2></div><span class="badge">服务端逐行校验</span></div>
    <p class="muted">批量创建、校验和执行都以当前项目、模型快照及不可变输入文件为准。</p>
    <p v-if="!actorId || !projectId || !canReadBatch" class="error-text" data-testid="batch-access-blocked">需要有效访问主体、项目和批量读取权限。</p>
    <p v-else-if="loading" class="placeholder">正在读取服务端 {{ chromatographyType.toUpperCase() }} 批量能力…</p>
    <p v-else-if="error && !capability" class="error-text" data-testid="batch-capability-error">{{ error }}</p>
    <template v-else-if="capability">
      <dl class="facts"><div><dt>类型健康</dt><dd>{{ capability.health.status }}<span v-if="capability.health.reason"> · {{ capability.health.reason }}</span></dd></div><div><dt>批量工具限制</dt><dd v-if="readyTool && budgetLimits" data-testid="batch-tool-limits">最多 {{ budgetLimits.max_evaluations }} 次计算 · 最长 {{ budgetLimits.max_runtime_seconds }} 秒；行数与候选范围以服务端模板校验为准。</dd><dd v-else>选择模型后读取对应批量工具与模板限制。</dd></div></dl>
      <label>服务端模型
        <select v-model="selectedModelKey" data-testid="batch-model-select" :disabled="!capabilityReady || busy || submissionLocked">
          <option value="">选择可用模型</option>
          <option v-for="model in models" :key="modelKey(model)" :value="modelKey(model)" :disabled="model.health.status !== 'READY' || models.filter((item) => modelKey(item) === modelKey(model)).length !== 1">
            {{ model.id }} · {{ model.scope }} · {{ model.version }} · schema {{ model.input_schema_version }}{{ model.preprocessing_profile_id ? ` · profile ${model.preprocessing_profile_id}` : '' }}{{ model.health.status !== 'READY' ? `（${model.health.reason || '暂不可用'}）` : '' }}
          </option>
        </select>
      </label>
      <p v-if="selectedModel && !readyTool" class="error-text" data-testid="batch-tool-blocked">{{ error || '服务端尚未声明此模型、profile、schema 与模板的唯一兼容绑定。' }}</p>
      <p v-if="readyTool" class="muted" data-testid="batch-tool-ready">批量工具已就绪；模板版本 {{ selectedTemplate?.version }}。</p>
      <p v-if="isLegacyTlc" class="warning-text" data-testid="legacy-template-readonly">TLC v1 仅只读；新建和执行只允许 tlc-batch-2。</p>
      <p v-if="error && capability" class="error-text" data-testid="batch-operation-error">{{ error }}</p>
      <div v-if="chromatographyType === 'cc' || chromatographyType === 'gc' || chromatographyType === 'sfc'" class="structure-preparation">
        <BatchStructurePreparation :project-id="projectId" :type="chromatographyType" :disabled="busy || Boolean(draft)" />
      </div>
      <div class="template-controls">
        <label>模板格式<select v-model="templateFormat" :disabled="busy || Boolean(draft)" @change="changeFormat"><option value="csv">CSV</option><option value="xlsx">XLSX</option></select></label>
        <button type="button" class="secondary" data-testid="download-batch-template" :disabled="!canDownloadTemplate" @click="downloadTemplate">{{ downloadingTemplate ? '正在下载…' : '下载服务端模板' }}</button>
      </div>
      <p v-if="selectedTemplate" class="muted">字段按服务端模板精确映射：{{ selectedTemplate.fields.join('、') }}</p>
      <section class="artifact-upload" aria-label="批量输入文件上传">
        <h3>上传批量输入</h3><p>填写服务端模板后上传；只有制品状态为 AVAILABLE 且扫描 PASSED 才能创建草稿。</p>
        <input ref="fileInput" type="file" :accept="downloadTemplateCsvOnly()" data-testid="batch-file-input" :disabled="!canSelectFile || Boolean(draft)" @change="selectFile" />
        <button type="button" class="primary" data-testid="upload-batch-file" :disabled="!canSelectFile || !selectedFile || busy" @click="uploadFile">{{ uploading ? '正在上传…' : '上传并检查' }}</button>
      </section>
      <section v-if="accepted || artifact" class="artifact-state" aria-label="输入制品状态">
        <h3>服务端制品状态</h3><p v-if="artifact">{{ artifact.status }} · 扫描 {{ artifact.scan_status }} · {{ artifact.size_bytes }} 字节</p><p v-else class="muted">正在等待服务端解析与扫描。</p>
        <p v-if="artifactReady" class="success-text">制品可用于批量草稿。</p><p v-else class="muted" data-testid="batch-artifact-blocker">{{ artifactBlockerMessage }}</p>
        <button type="button" class="secondary" :disabled="busy || !(accepted?.artifact_id || artifact?.id)" @click="refreshArtifactState">{{ refreshingArtifact ? '正在刷新…' : '刷新制品状态' }}</button>
        <JobStatus v-if="accepted?.job_id" :key="accepted.job_id" :job-id="accepted.job_id" />
      </section>
      <section class="draft-config" aria-label="批量配置">
        <h3>批量设置</h3>
        <label>草稿名称<input v-model="name" maxlength="200" :disabled="Boolean(draft) || busy" /></label>
        <label>无效行处理<select v-model="invalidRowPolicy" :disabled="draft ? !canEditDraft : !canCreateBatch || busy" @change="changePolicy"><option value="block">发现无效行时阻止提交</option><option value="exclude_invalid">只提交有效行</option></select></label>
        <div class="budget-grid"><label>最大计算次数<input v-model.number="maxEvaluations" type="number" min="1" :max="budgetLimits?.max_evaluations" :disabled="draft ? !canEditDraft : !canCreateBatch || busy" @change="changeBudget" /></label><label>最长运行秒数<input v-model.number="maxRuntimeSeconds" type="number" min="1" :max="budgetLimits?.max_runtime_seconds" :disabled="draft ? !canEditDraft : !canCreateBatch || busy" @change="changeBudget" /></label></div>
        <p v-if="invalidRowPolicy === 'exclude_invalid'" class="muted">验证完成后会显示并确认排除行数；这些行将标记 SKIPPED，不会计为执行失败。</p>
        <p v-if="!canCreateBatch" class="muted">当前项目权限未开放批量创建。</p>
        <button type="button" class="primary" data-testid="create-batch-draft" :disabled="!canCreateDraft" @click="createDraft">{{ creatingDraft ? '正在创建草稿…' : '创建批量草稿' }}</button>
      </section>
      <div v-if="draft" class="draft-workflow">
        <section class="draft-state" aria-label="批量草稿与服务端校验">
          <div class="panel-heading"><h3>批量草稿</h3><span class="badge">{{ draft.draft_status }} · revision {{ draft.revision }}</span></div>
          <dl class="facts"><div><dt>模型快照</dt><dd>{{ draft.model_selection.mode === 'exact' ? `${draft.model_selection.model_id} · ${draft.model_selection.model_version}` : 'Champion' }}</dd></div><div><dt>模板</dt><dd>{{ draft.template_version }}</dd></div><div><dt>草稿哈希</dt><dd>{{ draft.config_hash }}</dd></div></dl>
          <div class="actions"><button type="button" class="secondary" :disabled="!canValidateDraft" @click="validateDraft">{{ validating ? '正在提交校验…' : '提交服务端逐行校验' }}</button><button v-if="draft.validation_id" type="button" class="secondary" :disabled="busy || !canReadBatch" @click="refreshValidation()">{{ refreshingValidation ? '正在读取…' : '刷新校验与预览' }}</button></div>
          <JobStatus v-if="activeJobId" :key="activeJobId" :job-id="activeJobId" />
          <p v-if="validationMessage" class="muted" data-testid="validation-message">{{ validationMessage }}</p>
          <p v-if="validation" class="muted" data-testid="validation-counts">{{ validation.valid_rows }} 有效 · {{ validation.invalid_rows }} 无效 · {{ validation.excluded_rows }} 排除 · {{ validation.total_evaluations }} 次计算</p>
          <p v-if="validationError" class="error-text" data-testid="typed-preview-error">{{ validationError }}</p>
          <div v-if="validationRows.length" class="preview-list" data-testid="typed-preview"><h4>服务端行预览</h4>
            <article v-for="row in validationRows" :key="row.row_id" class="preview-row">
              <div class="row-heading"><strong>第 {{ row.source_row_number }} 行</strong><span>{{ row.status === 'INVALID' ? '无效，未执行' : row.status === 'WARNING' ? '有效 · 有提示' : '有效' }}</span></div>
              <p v-for="(line, index) in previewLines(row)" :key="index">{{ line }}</p>
              <ul v-if="row.field_errors?.length" class="field-errors"><li v-for="(item, index) in row.field_errors" :key="index">{{ Object.values(item).join('：') }}</li></ul>
              <p v-for="(warning, index) in row.warnings ?? []" :key="`${warning.code}:${index}`" class="muted">{{ warning.code }} · {{ warning.message }}</p>
            </article>
          </div>
          <p v-if="draft.invalid_row_policy === 'block' && validation?.invalid_rows" class="error-text">存在无效行，阻止策略下不会执行预测。</p>
          <section v-if="canUseValidation" class="confirmation-panel" aria-label="批量提交确认">
            <h4>提交前确认</h4><p>本次提交会绑定草稿 revision、模型 profile、validation、输入快照及排除行数。</p>
            <button type="button" class="secondary" :disabled="busy || !canCreateBatch" @click="loadSubmitPreview">{{ loadingConfirmation ? '正在读取…' : '读取服务端提交摘要' }}</button>
            <ul v-if="submitPreviewMatches && submitPreview" class="summary-list"><li v-for="item in submitPreview.summary" :key="item.label"><span>{{ item.label }}</span><strong>{{ item.value }}<small v-if="item.unit"> {{ item.unit }}</small></strong></li></ul>
            <p v-if="submitPreview?.blockers.length" class="error-text">{{ submitPreview.blockers.map((item) => item.message).join('；') }}</p>
            <button v-if="submitPreviewMatches && !submitConfirmed" type="button" class="secondary" :disabled="busy || !submitPreview?.can_confirm" @click="confirmSubmit">{{ creatingConfirmation ? '正在确认…' : '确认此版本' }}</button>
            <button type="button" class="primary" data-testid="submit-batch" :disabled="!canSubmit" @click="submitDraft">{{ submitting ? '正在提交…' : submissionLocked ? '请先刷新服务端状态' : '提交批量预测' }}</button>
          </section>
        </section>
        <section v-if="draft.job_id || resultRows.length" class="results-panel" aria-label="批量执行结果">
          <div class="panel-heading"><h3>执行结果</h3><span class="badge">{{ draft.execution_status ?? '尚未提交' }} · attempt {{ draft.attempt ?? 1 }}</span></div>
          <div class="actions"><button type="button" class="secondary" :disabled="busy || !canReadBatch" @click="refreshResults()">{{ refreshingResults ? '正在刷新…' : '刷新任务与行结果' }}</button><button v-if="canCancel" type="button" class="secondary" @click="cancelBatch">{{ canceling ? '正在取消…' : '取消任务' }}</button><label class="export-format">导出格式<select v-model="exportFormat" :disabled="busy || !canReadBatch || postProcessingLocked"><option value="csv">CSV</option><option value="json">JSON</option><option value="xlsx">XLSX</option></select></label><button type="button" class="secondary" :disabled="busy || !canReadBatch || postProcessingLocked" @click="createExport">{{ creatingExport ? '正在创建导出…' : `创建 ${exportFormat.toUpperCase()} 导出` }}</button></div>
          <p v-if="draft.counts.skipped" class="muted">{{ draft.counts.skipped }} 行已跳过，未运行模型，不计为预测失败。</p>
          <div v-if="resultRows.length" class="result-list" data-testid="batch-result-rows">
            <article v-for="row in resultRows" :key="`${row.row_id}:${row.attempt}`" class="result-row">
              <div class="row-heading"><strong>第 {{ row.source_row_number }} 行 · Attempt {{ row.attempt }}</strong><span>{{ resultStatus(row.status) }}</span></div>
              <p v-if="row.status === 'SKIPPED'">此行被排除，未执行预测。</p>
              <template v-if="row.status === 'SUCCEEDED' && resultCells(row).length"><div class="table-wrap"><table><tbody><tr v-for="cell in resultCells(row)" :key="cell.key"><th>{{ cell.key }}</th><td>{{ cell.value }}</td></tr></tbody></table></div></template>
              <p v-if="row.error?.message" class="error-text">{{ row.error.message }}</p>
              <p v-for="(warning, index) in row.warnings ?? []" :key="`${warning.code}:${index}`" class="muted">{{ warning.code }} · {{ warning.message }}</p>
              <label v-if="row.status === 'FAILED' && draft.execution_status !== 'RUNNING' && draft.execution_status !== 'QUEUED'" class="retry-choice"><input type="checkbox" :checked="selectedRetryRowsSet.has(row.row_id)" @change="toggleRow(row.row_id)" />选择此失败行重试</label>
            </article>
          </div>
          <p v-else class="muted">尚未读取逐行执行结果。</p>
          <section v-if="failedRows.length" class="retry-panel" aria-label="失败行重试">
            <h4>失败行重试</h4><p>重试只包含所选失败行，创建新的 Attempt；既有成功结果会保留。</p>
            <button type="button" class="secondary" :disabled="!canCreateRetryPlan" @click="makeRetryPlan">{{ creatingRetryPlan ? '正在生成计划…' : '创建失败行重试计划' }}</button>
            <div v-if="retryPlan" class="retry-plan"><p>{{ retryPlan.row_ids.length }} 行 · 预计 {{ retryPlan.estimated_evaluations }} 次计算 · attempt {{ retryPlan.expected_attempt }}</p>
              <button type="button" class="secondary" :disabled="busy || !canCreateBatch" @click="loadRetryPreview">{{ loadingRetryConfirmation ? '正在读取…' : '读取独立重试确认' }}</button>
              <ul v-if="retryPreviewMatches && retryPreview" class="summary-list"><li v-for="item in retryPreview.summary" :key="item.label"><span>{{ item.label }}</span><strong>{{ item.value }}<small v-if="item.unit"> {{ item.unit }}</small></strong></li></ul>
              <p v-if="retryPreview?.blockers.length" class="error-text">{{ retryPreview.blockers.map((item) => item.message).join('；') }}</p>
              <button v-if="retryPreviewMatches && !retryConfirmed" type="button" class="secondary" :disabled="busy || !retryPreview?.can_confirm" @click="confirmRetry">{{ creatingConfirmation ? '正在确认…' : '确认重试计划' }}</button>
              <button type="button" class="primary" :disabled="!canRetryFailed" @click="retryFailed">{{ retryingFailed ? '正在重试…' : '重试选中失败行' }}</button>
            </div>
          </section>
          <section v-if="exportView" class="export-panel" aria-label="批量导出状态">
            <h4>导出任务 · {{ exportView.status }}</h4><p>来源 revision {{ exportView.source_revision }} · attempt {{ exportView.source_attempt }}</p>
            <p v-if="exportView.error" class="error-text">{{ Object.values(exportView.error).join('；') }}</p>
            <div class="actions"><button type="button" class="secondary" :disabled="busy" @click="refreshExport">{{ refreshingExport ? '正在刷新…' : '刷新导出状态' }}</button><button type="button" class="secondary" :disabled="busy || exportView.status !== 'SUCCEEDED' || !exportView.artifact_id" @click="downloadExport">{{ downloadingExport ? '正在下载…' : '下载导出文件' }}</button></div>
          </section>
        </section>
      </div>
      <section v-else-if="initialBatchId || recoverableDraftId" class="recover-draft"><p>发现批量草稿 {{ initialBatchId || recoverableDraftId }}。恢复仅从服务端读取当前真相。</p><button type="button" class="secondary" :disabled="busy || !canReadBatch" @click="restoreBatchById(initialBatchId || recoverableDraftId)">{{ restoring ? '正在读取…' : '读取并恢复草稿' }}</button></section>
      <button v-if="draft || accepted || selectedFile" type="button" class="secondary" :disabled="!canStartNewBatch" @click="startNewBatch">新建批次</button>
      <p v-else class="workflow-gate">选择准确模型，下载对应模板，再上传已扫描通过的批量输入文件。</p>
    </template>
  </section>
</template>

<style scoped>
.batch-workspace { display: grid; gap: 14px; min-width: 0; }
.facts, .artifact-upload, .artifact-state, .draft-config, .draft-state, .confirmation-panel, .results-panel, .retry-panel, .export-panel, .recover-draft { min-width: 0; border: 1px solid var(--line); border-radius: 12px; padding: 12px; background: #fff; }
.facts { display: grid; gap: 8px; margin: 0; }.facts div { display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding: 5px 0; }.facts div:last-child { border-bottom: 0; }.facts dt { color: var(--muted); }.facts dd { margin: 0; overflow-wrap: anywhere; text-align: right; }
.artifact-upload, .artifact-state, .draft-config, .draft-state, .confirmation-panel, .results-panel, .retry-panel, .export-panel { display: grid; gap: 10px; }
.artifact-upload h3, .artifact-state h3, .draft-config h3, .draft-state h3, .confirmation-panel h4, .results-panel h3, .retry-panel h4, .export-panel h4 { margin: 0; }
.artifact-upload p, .recover-draft p, .muted, .workflow-gate { margin: 0; color: var(--muted); line-height: 1.5; }
.template-controls, .actions, .row-heading { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }.template-controls label { flex: 1 1 160px; }
.budget-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }.draft-workflow { display: grid; gap: 12px; }
.preview-list, .result-list { display: grid; gap: 9px; min-width: 0; }.preview-list h4 { margin: 0; }
.preview-row, .result-row { min-width: 0; border-top: 1px solid var(--line); padding: 9px 0; overflow-wrap: anywhere; }.preview-row p, .result-row p { margin: 5px 0; }
.row-heading { justify-content: space-between; }.field-errors { margin: 4px 0; padding-left: 20px; color: var(--danger, #b42318); }
.summary-list { margin: 0; padding-left: 20px; }.summary-list li { display: flex; justify-content: space-between; gap: 12px; }
.retry-choice { display: flex; align-items: center; gap: 7px; }.retry-choice input { width: auto; }.retry-plan { display: grid; gap: 9px; }
.copy-row code { overflow-wrap: anywhere; }.table-wrap { width: 100%; overflow-x: auto; }.table-wrap table { width: 100%; border-collapse: collapse; }
.table-wrap th, .table-wrap td { border-bottom: 1px solid var(--line); padding: 6px; text-align: left; vertical-align: top; }
.error-text { margin: 0; color: var(--danger, #b42318); }.warning-text { margin: 0; color: #8a5a00; }
@media (max-width: 640px) { .budget-grid { grid-template-columns: minmax(0, 1fr); }.facts div { flex-direction: column; gap: 2px; }.facts dd { text-align: left; }.template-controls, .actions, .row-heading { align-items: stretch; flex-direction: column; }.summary-list li { flex-direction: column; gap: 2px; } }
</style>
