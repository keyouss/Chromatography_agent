<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ConfirmationCard from '@/components/ConfirmationCard.vue'
import { apiClient, ApiClientError } from '@/core/api/client'
import type { ContractCreateRecordExportBody, ContractPrediction, ContractPredictionRecord, ContractSfcConditionsOptions } from '@/core/contract-types'
import type { ResourceRef } from '@/core/types'
import type { PredictionResultPayload } from '@/shared/prediction/result'
import { projectSelectionState, resolveProjectId } from '@/core/project-context'
import { useAppStore } from '@/stores/app'
import { useArtifactStore } from '@/stores/artifacts'
import { useConfirmationStore } from '@/stores/confirmations'
import { useRecordExportStore } from './export-store'
import { formatResultCell, gcPredictionModelSnapshotError, resultMetadataError, toSfcDisplayRow } from '@/shared/prediction/result'
import { isSfcScopeProvenance, sameSfcScopeProvenance } from '@/shared/prediction/sfc-provenance'
import { gcQuantileDisplayRows } from '@/shared/prediction/gc-quantiles'
import { useRecordsStore } from './store'
import { createReplayDraft } from '@/shared/prediction/replay'
import { useRouter } from 'vue-router'
import { activeProfile } from '@/app/generated/profile'

const store = useRecordsStore()
const app = useAppStore()
const confirmations = useConfirmationStore()
const exports = useRecordExportStore()
const artifacts = useArtifactStore()
const router = useRouter()
const selectedId = ref<string | null>(null)
const exportRecordIds = ref<string[]>([])
const exportFormat = ref<ContractCreateRecordExportBody['format']>('json')
const exportIncludes = ref<ContractCreateRecordExportBody['include']>(['input_snapshot', 'result', 'model_version', 'warnings'])
const retryIntent = ref(false)
const downloadError = ref<string | null>(null)
const downloading = ref(false)
const nextPageLoading = ref(false)
const selected = computed(() => store.records.find((item) => item.id === selectedId.value) ?? null)
const selectedRef = computed<ResourceRef | null>(() => selected.value ? { id: selected.value.id, type: 'prediction_record' } : null)
const archiving = ref(false)
const archiveError = ref<string | null>(null)
const detail = ref<ContractPredictionRecord | null>(null)
type RecordDetailPrediction = Omit<ContractPrediction, 'result'> & { result?: PredictionResultPayload | null }
const detailPrediction = ref<RecordDetailPrediction | null>(null)
const detailLoading = ref(false)
const detailPredictionError = ref(false)
const detailAssociationError = ref(false)
const detailSfcScopeError = ref<string | null>(null)
const detailSfcScopePending = ref(false)
let detailGeneration = 0
const replaying = ref(false)
const replayError = ref<string | null>(null)
const confirmationMatches = computed(() => Boolean(selectedRef.value && confirmations.isFor(selectedRef.value, 'resource.archive')))
const projectId = computed(() => resolveProjectId(app.accessContext, null))
const allowedActions = computed(() => app.accessContext?.allowed_actions ?? [])
const canReplay = computed(() => Boolean(projectId.value && (allowedActions.value.includes('*') || (allowedActions.value.includes('prediction:read') && allowedActions.value.includes('prediction:create')))))
const projectState = computed(() => projectSelectionState(app.accessContext))
const batchDetailLocation = computed(() => {
  const record = detail.value
  if (!record?.batch_id || record.result_ref?.type !== 'batch_prediction' || record.result_ref.id !== record.batch_id
    || record.project_id !== projectId.value || !app.accessContext?.accessible_projects.some((project) => project.id === record.project_id)
    || !activeProfile.modules.includes('prediction') || !activeProfile.chromatographyTypes.includes(record.chromatography_type)
    || !activeProfile.predictionTools[record.chromatography_type]?.includes('batch')) return null
  return { path: `/prediction/${record.chromatography_type}/batch`, query: { batch_id: record.batch_id, project_id: record.project_id } }
})
const batchDetailMessage = computed(() => {
  const record = detail.value
  if (!record?.batch_id) return ''
  if (record.project_id !== projectId.value || !app.accessContext?.accessible_projects.some((project) => project.id === record.project_id)) return '记录不属于当前可访问项目，无法打开批量详情。'
  if (record.result_ref?.type !== 'batch_prediction' || record.result_ref.id !== record.batch_id) return '服务端批量结果引用与记录不一致，无法打开批量详情。'
  return batchDetailLocation.value ? '此记录来自批量任务，可读取整批结果及各行状态。' : '当前构建未包含此色谱类型的批量详情；记录快照仍可读取。'
})
const contractExportFormats: ContractCreateRecordExportBody['format'][] = ['json', 'csv', 'xlsx']
const availableExportFormats = computed(() => {
  const advertised = app.capabilities?.export_formats
  if (!Array.isArray(advertised)) return []
  return contractExportFormats.filter((format) => advertised.includes(format))
})
const exportCapabilityMessage = computed(() => {
  if (!app.capabilities) return app.state === 'loading' || app.state === 'idle' ? '正在读取服务端导出能力…' : '未能读取服务端导出能力，当前不可导出。'
  return availableExportFormats.value.length ? '' : '服务端当前未声明合同支持的导出格式，当前不可导出。'
})
const canSubmitExport = computed(() => {
  if (!projectId.value || !availableExportFormats.value.length || exports.busy || exports.accessDenied || ['loading', 'submitting'].includes(exports.state) || exports.isPending) return false
  if (exports.pendingSubmission) return availableExportFormats.value.includes(exports.pendingSubmission.body.format)
  return availableExportFormats.value.includes(exportFormat.value) && selectedExportCount.value > 0 && exportIncludes.value.length > 0
})
const allRecordsSelected = computed(() => store.records.length > 0 && store.records.every((record) => exportRecordIds.value.includes(record.id)))
const selectedExportCount = computed(() => exportRecordIds.value.length)
const exportJobRef = computed<ResourceRef | null>(() => exports.job ? { type: 'job', id: exports.job.id } : null)
const retryConfirmationMatches = computed(() => Boolean(exportJobRef.value && confirmations.isFor(exportJobRef.value, 'job.retry')))
const exportStatus = computed(() => exports.exportView?.status ?? exports.accepted?.status ?? '')
const exportCanDownload = computed(() => exports.state === 'ready' && !exports.accessDenied && exports.artifact?.status === 'AVAILABLE' && exports.artifact.download_allowed)
let exportPollTimer: number | null = null
let replayGeneration = 0
watch(projectId, () => { replayGeneration += 1; replaying.value = false; replayError.value = null })

watch(availableExportFormats, (formats) => {
  if (formats.length && !formats.includes(exportFormat.value)) exportFormat.value = formats[0]
}, { immediate: true })

type DetailRow = { label: string; value: string }
type DetailRecord = Record<string, unknown>
function asRecord(value: unknown): DetailRecord { return value && typeof value === 'object' && !Array.isArray(value) ? value as DetailRecord : {} }
function textValue(value: unknown): string { return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean' ? String(value) : '' }
function stringList(value: unknown): string[] { return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.length > 0) : [] }
function solventLabel(value: unknown): string { return typeof value === 'string' ? value.replaceAll('_', ' ') : '' }
function inputRows(type: string, input: unknown, taskType?: string): DetailRow[] {
  const source = asRecord(input)
  if (type === 'tlc' && taskType === 'tlc_condition_comparison') {
    return Array.isArray(source.conditions) ? source.conditions.flatMap((item, index) => {
      const components = asRecord(item).solvent_components
      if (!Array.isArray(components)) return []
      const values = components.flatMap((componentValue) => {
        const component = asRecord(componentValue)
        return typeof component.solvent_id === 'string' && typeof component.fraction === 'number' && Number.isFinite(component.fraction)
          ? [`${solventLabel(component.solvent_id)} ${(component.fraction * 100).toFixed(2)}%`]
          : []
      })
      return values.length ? [{ label: `比较条件 ${index + 1}`, value: values.join('；') }] : []
    }) : []
  }
  if (type === 'tlc' && taskType === 'tlc_reverse_solvent') {
    return [
      ...(typeof source.target_rf_min === 'number' && typeof source.target_rf_max === 'number' ? [{ label: '目标 Rf 范围', value: `${source.target_rf_min}–${source.target_rf_max}` }] : []),
      ...(Array.isArray(source.candidate_system_ids) ? [{ label: '候选溶剂体系', value: source.candidate_system_ids.map(textValue).filter(Boolean).join('、') }] : []),
      ...(typeof source.ratio_step === 'number' ? [{ label: '比例步长', value: textValue(source.ratio_step) }] : []),
      ...(typeof source.max_candidates === 'number' ? [{ label: '最大候选数', value: textValue(source.max_candidates) }] : []),
    ]
  }
  const keysByType: Record<string, [string, string][]> = {
    tlc: [['solvent_components', '展开剂组成']],
    cc: [['column_spec_id', '色谱柱规格'], ['eluent_system_id', '洗脱体系'], ['flow_rate_ml_min', '流速 (mL/min)'], ['loading_solvent_id', '上样溶剂'], ['loading_solvent_volume_ul', '上样溶剂体积 (µL)'], ['sample_mass_mg', '样品质量 (mg)']],
    hplc: [['column_ids', '色谱柱'], ['flow_rate_ml_min', '流速 (mL/min)'], ['isopropanol_fraction', '异丙醇比例']],
    gc: [['temperature_program', '温度程序']],
    sfc: [['column_id', '色谱柱'], ['modifier_components', '改性剂组成'], ['modifier_total_fraction', '改性剂总比例'], ['top_k', '返回上限（Top-k）']],
  }
  return (keysByType[type] ?? []).flatMap(([key, label]) => {
    if (!(key in source)) return []
    const value = source[key]
    if (key === 'solvent_components' || key === 'modifier_components') {
      const components = Array.isArray(value) ? value.flatMap((item) => {
        const component = asRecord(item)
        const id = component.solvent_id ?? component.structure_ref
        const fraction = component.fraction
        return typeof id === 'string' && typeof fraction === 'number' && Number.isFinite(fraction) ? [`${solventLabel(id)} ${(fraction * 100).toFixed(2)}%`] : []
      }) : []
      return [{ label, value: components.length ? components.join('；') : '服务端未返回有效组成' }]
    }
    if (key === 'temperature_program') {
      const program = asRecord(value)
      const mode = textValue(program.mode)
      const parts = mode === 'linear'
        ? [['initial_temperature_c', '初始温度'], ['initial_hold_min', '初始保持'], ['heating_rate_c_per_min', '升温速率'], ['final_temperature_c', '终温']].flatMap(([field, title]) => textValue(program[field]) ? [`${title} ${textValue(program[field])}${field === 'heating_rate_c_per_min' ? ' °C/min' : field.endsWith('_c') ? ' °C' : ' min'}`] : [])
        : Array.isArray(program.temperature_series_c) ? [`温度序列 ${program.temperature_series_c.filter((item): item is number => typeof item === 'number' && Number.isFinite(item)).join(' → ')} °C`] : []
      return [{ label, value: parts.length ? parts.join('；') : '服务端未返回完整温度程序' }]
    }
    if (Array.isArray(value)) return [{ label, value: value.map(textValue).filter(Boolean).join('、') || '服务端未返回' }]
    return [{ label, value: textValue(value) || '服务端未返回' }]
  })
}
const detailSnapshot = computed(() => detailPrediction.value?.input_snapshot ?? asRecord(detail.value?.input_snapshot) as unknown as ContractPrediction['input_snapshot'])
const detailStructures = computed(() => stringList(detailSnapshot.value?.normalized_structures))
const detailStructureRefs = computed(() => stringList(detailSnapshot.value?.structure_refs))
const detailInputRows = computed(() => inputRows(detail.value?.chromatography_type ?? '', detailSnapshot.value?.input, detailPrediction.value?.result?.task_type))
const detailResult = computed(() => detailPrediction.value?.result ?? null)
const detailSfcResultError = computed(() => {
  if (detailPrediction.value?.chromatography_type !== 'sfc') return null
  const result = asRecord(detailResult.value)
  const hasScopeMetadata = result.candidate_scope_provenance != null || result.evaluated_candidate_count != null
    || (Array.isArray(result.rows) && result.rows.some((row) => asRecord(asRecord(row).conditions).observed_in_source != null))
  // Historical records retain the existing neutral score display when no new scope metadata was saved.
  return detailSfcScopeError.value ?? (hasScopeMetadata ? resultMetadataError(detailResult.value, 'sfc') : null)
})
const detailSfcCounts = computed(() => {
  if (detailResult.value?.task_type !== 'sfc' || detailSfcResultError.value || detailSfcScopePending.value) return []
  const result = asRecord(detailResult.value)
  const provenance = result.candidate_scope_provenance
  return [
    { label: '冻结范围数量', value: isSfcScopeProvenance(provenance) ? String(provenance.candidate_count) : '历史结果未记录' },
    { label: '实际评估数量', value: typeof result.evaluated_candidate_count === 'number' ? String(result.evaluated_candidate_count) : '历史结果未记录' },
    { label: '实际返回条数', value: Array.isArray(detailResult.value.rows) ? String(detailResult.value.rows.length) : '服务端未返回' },
    ...(isSfcScopeProvenance(provenance) ? [
      { label: '范围中目录已有组合', value: String(provenance.observed_count) },
      { label: '范围中目录未见组合', value: String(provenance.unobserved_count) },
    ] : []),
  ]
})
const detailCcResultError = computed(() => {
  const linked = detailPrediction.value
  return linked?.chromatography_type === 'cc' ? resultMetadataError(linked.result, 'cc') : null
})
const detailGcResultError = computed(() => {
  const linked = detailPrediction.value
  if (linked?.chromatography_type !== 'gc') return null
  const snapshotError = gcPredictionModelSnapshotError(linked.model, linked.schema_version)
  if (snapshotError) return snapshotError
  const metadataError = resultMetadataError(linked.result, 'gc')
  if (metadataError) return metadataError
  if (linked.result?.task_type === 'gc' && linked.result.rows.some((row) => gcQuantileDisplayRows(asRecord(row).quantiles) === null)) {
    return 'GC 分位数字段不符合 Q10、Q50、Q90 结果展示约定。'
  }
  const outputSchemaVersion = linked.model?.output_schema_version
  if (typeof outputSchemaVersion !== 'string' || !outputSchemaVersion.trim()) return 'GC 模型缺少输出 schema 身份。'
  if (linked.result?.schema_version !== outputSchemaVersion) return 'GC 结果 schema 与该历史预测的模型输出 schema 不匹配。'
  return null
})
const detailModel = computed(() => {
  const saved = asRecord(detail.value?.model_snapshot)
  const resolved = detailPrediction.value?.model
  const field = (key: 'id' | 'version' | 'artifact_sha256' | 'input_schema_version' | 'output_schema_version') => textValue(resolved?.[key]) || textValue(saved[key]) || '服务端未返回'
  return [
    { label: '模型 ID', value: field('id') }, { label: '模型版本', value: field('version') },
    { label: '制品 SHA-256', value: field('artifact_sha256') }, { label: '输入 Schema', value: field('input_schema_version') },
    { label: '输出 Schema', value: field('output_schema_version') },
  ]
})
const detailResultRows = computed<DetailRecord[]>(() => {
  const result = detailPrediction.value?.result
  if (detailCcResultError.value || detailGcResultError.value || detailSfcResultError.value || detailSfcScopePending.value) return []
  if (result?.task_type === 'tlc_condition_comparison') return Array.isArray(result.results) ? result.results.map(asRecord) : []
  if (result?.task_type === 'tlc_reverse_solvent') return Array.isArray(result.candidates) ? result.candidates.map(asRecord) : []
  if (!result || !('rows' in result) || !Array.isArray(result.rows)) return []
  return result.rows.flatMap((row) => {
    const record = asRecord(row)
    if (detailPrediction.value?.chromatography_type === 'sfc') return [toSfcDisplayRow(record)]
    if (detailPrediction.value?.chromatography_type !== 'gc') return [record]
    const quantiles = gcQuantileDisplayRows(record.quantiles) ?? []
    return [
      { metric: '预测保留时间', value: record.retention_time_min, unit: 'min' },
      ...quantiles,
    ]
  })
})
const resultLabels: Record<string, string> = { rf: 'Rf', predicted_rf: '预测 Rf', retention_time_min: '保留时间 (min)', metric: '指标', value: '保留时间', unit: '单位', quantiles: '分位数保留时间 (min)', volume_ml: '体积 (mL)', time_seconds: '时间 (s)', flow_rate_ml_min: '流速 (mL/min)', candidate_id: '候选 ID', separation: '得分', separation_probability: '分离概率', rank: '排名', score_semantics: '分数语义' }
const detailResultFields = computed(() => [...new Set(detailResultRows.value.flatMap((row) => Object.keys(row)))].filter((field) => detailPrediction.value?.chromatography_type !== 'sfc' || field !== 'score_semantics'))
function resultFieldLabel(field: string): string {
  if (detailPrediction.value?.chromatography_type === 'sfc') {
    const sfcLabels: Record<string, string> = { column: '色谱柱', stationary_phase: '固定相', mode: '模式', length_mm: '柱长 (mm)', inner_diameter_mm: '内径 (mm)', particle_size_um: '粒径 (µm)', column_temperature_c: '柱温 (°C)', flow_rate_ml_min: '流速 (mL/min)', observed_in_source: '条件目录中已有' }
    if (sfcLabels[field]) return sfcLabels[field]
  }
  if (field === 'separation') {
    const resultSemantics = asRecord(detailResult.value).score_semantics
    const rowSemantics = detailResultRows.value.filter((row) => 'separation' in row).map((row) => row.score_semantics)
    const declaredRows = rowSemantics.filter((value) => value !== undefined && value !== null)
    const distinctRows = [...new Set(declaredRows)]
    const semantics = resultSemantics !== undefined && resultSemantics !== null
      ? rowSemantics.some((value) => value !== undefined && value !== null && value !== resultSemantics) ? null : resultSemantics
      : distinctRows.length === 1 && declaredRows.length === rowSemantics.length ? distinctRows[0] : null
    if (semantics === 'positive_class_probability') return '正类概率'
    if (semantics === 'separation_probability') return '分离概率'
  }
  return resultLabels[field] ?? field
}
function resultCell(value: unknown, field?: string): string {
  if (value === null || value === undefined) return '服务端未返回'
  return formatResultCell(value, field)
}
type DetailMetricRow = { key: string; label: string; value: string; unit: string; origin: string }
const showHplcMetrics = computed(() => detailResult.value?.task_type === 'hplc' && detailPrediction.value?.chromatography_type === 'hplc')
const detailMetricRows = computed<DetailMetricRow[]>(() => {
  const result = detailResult.value
  if (!result || result.task_type !== 'hplc' || !Array.isArray(result.metrics)) return []
  return result.metrics.map((item) => {
    const metric = asRecord(item)
    const key = textValue(metric.key) || '服务端未返回'
    const rawUnit = metric.unit
    const unit = rawUnit === null || rawUnit === undefined || rawUnit === ''
      ? '服务端未声明'
      : typeof rawUnit !== 'string'
        ? '未知单位'
        : ['min', 'probability'].includes(rawUnit) ? rawUnit : `未知单位：${rawUnit}`
    const rawOrigin = textValue(metric.origin)
    const origin = rawOrigin === 'model' ? 'model（模型返回）' : rawOrigin === 'derived' ? 'derived（服务端派生）' : rawOrigin ? `未知来源：${rawOrigin}` : '服务端未返回'
    return {
      key,
      label: textValue(metric.label) || key,
      value: metric.value === null || metric.value === undefined ? '服务端未返回' : resultCell(metric.value),
      unit,
      origin,
    }
  })
})
const detailApplicability = computed(() => {
  const result = asRecord(detailResult.value)
  return asRecord(result.applicability)
})
const detailResultWarnings = computed(() => {
  const result = detailResult.value
  if (result?.task_type !== 'tlc_condition_comparison' && result?.task_type !== 'tlc_reverse_solvent') return { items: [] as string[], error: '' }
  const hasWarnings = Object.prototype.hasOwnProperty.call(result, 'warnings')
  if (result.task_type === 'tlc_condition_comparison' && !hasWarnings) return { items: [] as string[], error: '' }
  if (!Array.isArray(result.warnings) || result.warnings.some((item) => typeof item !== 'string')) {
    return { items: [] as string[], error: '结果警告格式不符合契约，无法确认完整警告。' }
  }
  return { items: result.warnings, error: '' }
})
const detailWarnings = computed(() => {
  const recordWarnings = (detail.value?.warnings ?? []).map((message) => ({ code: '', message }))
  if (detailPrediction.value?.chromatography_type === 'gc' && detailGcResultError.value) return recordWarnings
  const warningItems = [...(detailPrediction.value?.warnings ?? []), ...(Array.isArray(detailApplicability.value.warnings) ? detailApplicability.value.warnings : []), ...detailResultWarnings.value.items]
  const warnings = [...recordWarnings, ...warningItems.flatMap((item) => {
    if (typeof item === 'string') return [{ code: '', message: item }]
    const warning = asRecord(item)
    return typeof warning.message === 'string' ? [{ code: textValue(warning.code), message: warning.message }] : []
  })]
  return warnings.filter((warning, index) => warnings.findIndex((item) => item.code === warning.code && item.message === warning.message) === index)
})
const detailError = computed(() => detailPrediction.value?.error ?? null)

async function refreshRecords() {
  if (!app.accessContext) await app.load()
  await store.list(projectId.value)
}

function toggleVisibleExports(checked: boolean) {
  const visible = store.records.map((record) => record.id)
  exportRecordIds.value = checked
    ? [...new Set([...exportRecordIds.value, ...visible])]
    : exportRecordIds.value.filter((id) => !visible.includes(id))
}

async function createRecordsExport() {
  if (exports.pendingSubmission) {
    if (!availableExportFormats.value.includes(exports.pendingSubmission.body.format)) return
    await exports.create(exports.pendingSubmission.body)
    return
  }
  if (!projectId.value || !availableExportFormats.value.includes(exportFormat.value) || selectedExportCount.value === 0 || exportIncludes.value.length === 0) return
  retryIntent.value = false
  await exports.create({
    project_id: projectId.value,
    resource_refs: exportRecordIds.value.map((id) => ({ type: 'prediction_record', id })),
    format: exportFormat.value,
    include: [...exportIncludes.value],
  })
}

async function downloadExport() {
  const artifact = exports.artifact
  if (!exportCanDownload.value || !artifact || artifact.kind !== 'export_report') return
  const requestProject = exports.projectId
  const requestExport = exports.exportId
  downloading.value = true; downloadError.value = null
  try {
    const response = await artifacts.downloadArtifact(artifact.id)
    const url = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = artifact.file_name.replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').trim() || `prediction-records-export.${exports.exportView?.format ?? 'json'}`
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (reason) {
    downloadError.value = '制品下载暂不可用，请刷新后重试。'
    if (reason instanceof ApiClientError && [401, 403, 404].includes(reason.status)) {
      exports.invalidateAccess('当前导出或制品不可下载；旧操作已停用。', requestProject, requestExport)
    }
  }
  finally { downloading.value = false }
}

async function loadNextRecordPage() {
  const cursor = store.page?.next_cursor
  if (!projectId.value || !cursor || nextPageLoading.value) return
  nextPageLoading.value = true
  try { await store.list(projectId.value, cursor) } catch { /* the records store retains the public read error */ }
  finally { nextPageLoading.value = false }
}

async function retryExport() {
  if (!retryConfirmationMatches.value || !confirmations.confirmation) return
  await exports.retry(confirmations.confirmation.id)
  retryIntent.value = false
}
function prepareExportRetry() { selectedId.value = null; retryIntent.value = true }

async function archiveSelected() {
  if (!selected.value || !selectedRef.value || !confirmations.confirmation || !confirmationMatches.value) return
  archiving.value = true; archiveError.value = null
  try {
    await store.archive(selected.value.id, confirmations.confirmation.id)
    await store.list(projectId.value)
  } catch (reason) { archiveError.value = reason instanceof Error ? reason.message : '归档暂不可用' }
  finally { archiving.value = false }
}

async function replayRecord(recordId: string) {
  if (!projectId.value || !canReplay.value || replaying.value) return
  replaying.value = true; replayError.value = null
  const generation = replayGeneration
  const expectedProject = projectId.value
  const isCurrent = () => replayGeneration === generation && projectId.value === expectedProject
  try {
    const draft = await createReplayDraft(recordId, expectedProject)
    if (!isCurrent()) return
    await router.push({ path: `/prediction/${encodeURIComponent(draft.chromatography_type)}`, query: { draft_id: draft.id } })
  } catch (reason) {
    if (!isCurrent()) return
    if (reason instanceof ApiClientError && [403, 404].includes(reason.status)) replayError.value = '当前记录不可重放；服务端未授予访问权限或记录已失效。'
    else replayError.value = reason instanceof Error ? reason.message : '创建重放草稿失败'
  } finally { if (isCurrent()) replaying.value = false }
}

async function loadDetail(id: string) {
  const generation = ++detailGeneration
  const expectedProject = projectId.value
  const isCurrent = () => generation === detailGeneration && expectedProject === projectId.value
  detailLoading.value = true; archiveError.value = null; replayError.value = null; detail.value = null; detailPrediction.value = null; detailPredictionError.value = false; detailAssociationError.value = false
  detailSfcScopeError.value = null; detailSfcScopePending.value = false
  try {
    const loadedRecord = await store.get(id)
    if (!isCurrent()) return
    detail.value = loadedRecord
    if (loadedRecord.batch_id) return
    if (detail.value.prediction_id) {
      try {
        const prediction = await store.getPrediction(detail.value.prediction_id)
        if (!isCurrent()) return
        if (prediction.id !== detail.value.prediction_id || prediction.prediction_record_id !== detail.value.id
          || prediction.project_id !== expectedProject || detail.value.project_id !== expectedProject
          || prediction.chromatography_type !== detail.value.chromatography_type) detailAssociationError.value = true
        else {
          const provenance = asRecord(prediction.result).candidate_scope_provenance
          detailSfcScopePending.value = prediction.chromatography_type === 'sfc' && isSfcScopeProvenance(provenance)
          detailPrediction.value = prediction
          if (prediction.chromatography_type === 'sfc' && isSfcScopeProvenance(provenance)) {
            try {
              const inputScope = asRecord(prediction.input_snapshot.input).candidate_scope_id
              if (inputScope !== provenance.scope_id) throw new Error('scope mismatch')
              const query = new URLSearchParams({ project_id: expectedProject!, scope_id: provenance.scope_id })
              const options = await apiClient.get<ContractSfcConditionsOptions>(`/sfc/conditions/options?${query.toString()}`)
              if (!isCurrent()) return
              if (options.project_id !== expectedProject || options.scope_id !== provenance.scope_id
                || options.model_id !== prediction.model.id || options.model_version !== prediction.model.version
                || options.candidate_count !== provenance.candidate_count || !sameSfcScopeProvenance(options.scope_provenance, provenance)) {
                detailSfcScopeError.value = '结果来源与冻结候选范围不一致；结果已隐藏。'
              }
            } catch { if (isCurrent()) detailSfcScopeError.value = '候选范围来源核验失败；快照已保留，请重新读取详情。' }
            finally { if (isCurrent()) detailSfcScopePending.value = false }
          }
        }
      }
      catch { if (isCurrent()) detailPredictionError.value = true }
    }
  }
  catch (reason) { if (isCurrent()) archiveError.value = reason instanceof Error ? reason.message : '记录详情暂不可用' }
  finally { if (isCurrent()) detailLoading.value = false }
}

onMounted(() => { void refreshRecords().catch(() => undefined) })
watch(projectId, (value) => { exportRecordIds.value = []; if (value) void exports.restore(value) }, { immediate: true })
watch(() => exports.pendingSubmission, (pending) => {
  if (!pending || pending.body.project_id !== projectId.value) return
  exportRecordIds.value = pending.body.resource_refs.filter((ref) => ref.type === 'prediction_record').map((ref) => ref.id)
  exportFormat.value = pending.body.format
  exportIncludes.value = [...pending.body.include]
}, { immediate: true })
watch(projectId, () => { exportRecordIds.value = []; detailGeneration++; detailLoading.value = false; detail.value = null; detailPrediction.value = null; detailSfcScopePending.value = false; detailSfcScopeError.value = null })
watch(() => exports.isPending, (pending) => {
  if (exportPollTimer !== null) window.clearInterval(exportPollTimer)
  exportPollTimer = pending ? window.setInterval(() => { void exports.refresh() }, 2500) : null
}, { immediate: true })
onBeforeUnmount(() => { replayGeneration += 1; detailGeneration++; if (exportPollTimer !== null) window.clearInterval(exportPollTimer) })
</script>

<template>
  <div class="view records-view">
    <header class="page-header"><span class="eyebrow">DATA & KNOWLEDGE · RECORDS</span><h1>预测记录</h1><p>记录列表、revision、ETag 与归档操作均以服务端响应为准。</p></header>
    <p v-if="replayError" class="panel error-text" role="alert" data-testid="replay-record-error">{{ replayError }}</p>
    <section class="panel">
      <div v-if="app.state === 'loading'" class="placeholder">正在读取访问上下文…</div>
      <div v-else-if="projectState === 'needs_selection'" class="error-text">当前访问上下文需要选择项目，预测记录暂不可读取。</div>
      <div v-else-if="projectState === 'blocked'" class="error-text">当前访问上下文没有可访问项目，预测记录暂不可读取。</div>
      <div v-else-if="store.state === 'loading'" class="placeholder">正在读取预测记录…</div>
      <div v-else-if="store.state === 'error'" class="error-text">{{ store.error }}</div>
      <div v-else-if="!store.records.length" class="placeholder">服务端没有返回记录；不会展示静态样例。</div>
      <div v-else class="records-table-wrap" role="region" aria-label="预测记录表格，可横向滚动" tabindex="0" data-testid="records-table-scroll"><table><thead><tr><th><input aria-label="全选当前页记录用于导出" type="checkbox" :checked="allRecordsSelected" :disabled="Boolean(exports.pendingSubmission)" @change="toggleVisibleExports(($event.target as HTMLInputElement).checked)"></th><th>ID</th><th>类型</th><th>状态</th><th>revision</th><th>操作</th></tr></thead><tbody><tr v-for="record in store.records" :key="record.id"><td><input v-model="exportRecordIds" type="checkbox" :disabled="Boolean(exports.pendingSubmission)" :value="record.id" :aria-label="`选择记录 ${record.id} 用于导出`"></td><td>{{ record.id }}</td><td>{{ record.chromatography_type }}</td><td>{{ record.status }}</td><td>{{ record.revision }}</td><td><button class="secondary" @click="selectedId = record.id; retryIntent = false">选择归档</button><button class="secondary" :disabled="detailLoading" @click="loadDetail(record.id)">{{ detailLoading ? '读取中…' : '读取详情' }}</button><button class="secondary" data-testid="replay-record-row" :disabled="!canReplay || replaying" @click="replayRecord(record.id)">重新预测</button></td></tr></tbody></table></div>
      <div v-if="store.page?.has_more" class="muted">还有更多记录；已选项目会与后续页面的选择合并。<button class="secondary" :disabled="nextPageLoading" @click="loadNextRecordPage">{{ nextPageLoading ? '读取中…' : '加载下一页' }}</button></div>
      <div class="export-workspace" data-testid="record-export">
        <h2>导出预测记录</h2>
        <p class="muted">导出仅包含已勾选记录；制品由服务端生成，刷新后可恢复最近一次导出状态。</p>
        <p v-if="exportCapabilityMessage" class="warning-text" role="status" data-testid="record-export-capability">{{ exportCapabilityMessage }}</p>
        <p v-if="exports.pendingSubmission && !availableExportFormats.includes(exports.pendingSubmission.body.format)" class="warning-text" role="status">待确认请求的格式已不再由服务端提供，暂不能安全重试。</p>
        <div class="export-options">
          <label>格式<select v-model="exportFormat" aria-label="导出格式" :disabled="Boolean(exports.pendingSubmission) || !availableExportFormats.length"><option v-if="!availableExportFormats.length" disabled value="">暂不可用</option><option v-for="format in availableExportFormats" :key="format" :value="format">{{ format.toUpperCase() }}</option></select></label>
          <label v-for="field in (['input_snapshot', 'result', 'model_version', 'warnings'] as const)" :key="field" class="include-option"><input v-model="exportIncludes" type="checkbox" :disabled="Boolean(exports.pendingSubmission) || !availableExportFormats.length" :value="field">{{ { input_snapshot: '输入快照', result: '正式结果', model_version: '模型版本', warnings: '警告' }[field] }}</label>
        </div>
        <p class="muted">已选择 {{ selectedExportCount }} 条 · 仍有下一页时须先逐页选择。</p>
        <p v-if="exports.pendingSubmission" class="warning-text" role="status">{{ exports.error || '上次提交状态未知；请使用同一选择安全重试。' }}</p>
        <p v-if="!exportStatus && exports.state === 'error'" class="error-text" data-testid="record-export-state-error">{{ exports.error }}</p>
        <button v-if="!exportStatus && exports.exportId" class="secondary" :disabled="exports.busy || exports.state === 'loading'" data-testid="record-export-reload" @click="exports.refresh">重新读取导出状态</button>
        <button class="primary" :disabled="!canSubmitExport" data-testid="create-record-export" @click="createRecordsExport">{{ exports.state === 'submitting' ? '提交中…' : exports.pendingSubmission ? '使用相同幂等键安全重试' : '创建导出' }}</button>
        <section v-if="exportStatus" class="export-status" aria-live="polite" data-testid="record-export-status">
          <h3>最近一次导出</h3>
          <p>导出 {{ exports.exportId }} · {{ exportStatus }}<template v-if="exports.exportView"> · {{ exports.exportView.format.toUpperCase() }} · 格式版本 {{ exports.exportView.format_version }}</template></p>
          <p v-if="exports.job">任务 {{ exports.job.id }} · {{ exports.job.status }} · Attempt {{ exports.job.attempt }}</p>
          <p v-if="exports.state === 'loading'" class="muted">正在读取服务端状态…</p>
          <p v-if="exports.error" class="error-text">{{ exports.error }}</p>
          <p v-if="exports.exportView?.status === 'FAILED'" class="error-text">服务端报告导出失败；不会提供未生成的文件。</p>
          <p v-if="exports.exportView?.status === 'CANCELED'" class="muted">导出任务已取消。</p>
          <p v-if="exports.exportView?.status === 'TIMED_OUT' || exports.exportView?.status === 'INTERRUPTED'" class="warning-text">任务状态：{{ exports.exportView.status }}。</p>
          <p v-if="exports.exportView?.status === 'SUCCEEDED' && !exports.exportView.artifact_id" class="warning-text">服务端未返回导出制品 ID。</p>
          <p v-if="exports.exportView?.artifact_id && !exports.artifact && exports.exportView.status === 'SUCCEEDED'" class="muted">{{ exports.artifactError || '正在确认服务端制品状态…' }}</p>
          <p v-if="exports.artifact && !exportCanDownload" class="muted">制品状态 {{ exports.artifact.status }}；服务端当前不允许下载。</p>
          <button v-if="exportCanDownload" class="primary" :disabled="downloading" data-testid="download-record-export" @click="downloadExport">{{ downloading ? '下载中…' : `下载 ${exports.artifact?.file_name ?? '导出文件'}` }}</button>
          <p v-if="downloadError" class="error-text">{{ downloadError }}</p>
          <button class="secondary" :disabled="exports.busy || exports.state === 'loading'" @click="exports.refresh">刷新导出状态</button>
          <button v-if="exports.canCancel" class="secondary" :disabled="exports.busy" @click="exports.cancel">{{ exports.job?.status === 'CANCEL_REQUESTED' ? '正在取消…' : '取消导出' }}</button>
          <button v-if="exports.canRetry && !retryIntent" class="secondary" :disabled="exports.busy" @click="prepareExportRetry">准备重试（需服务端确认）</button>
          <template v-if="exports.canRetry && retryIntent && exportJobRef">
            <ConfirmationCard :resource-ref="exportJobRef" action="job.retry" />
            <button class="primary" :disabled="exports.busy || !retryConfirmationMatches" @click="retryExport">确认后重试</button>
          </template>
        </section>
      </div>
    </section>
    <section v-if="detail" class="panel record-detail" data-testid="record-detail">
      <h2>记录详情 {{ detail.id }}</h2>
      <p v-if="detailLoading" class="muted" role="status">正在读取关联预测，结果、警告和模型来源尚未确认。</p>
      <template v-else>
      <section v-if="!detail.batch_id" class="detail-block replay-action"><p class="muted">从服务端记录创建可编辑的预测草稿；旧模型不可用时，预测页会明确阻止提交。</p><button class="primary" data-testid="replay-record" :disabled="!canReplay || replaying" @click="replayRecord(detail.id)">{{ replaying ? '创建草稿中…' : '基于此记录重新预测' }}</button><p v-if="!canReplay" class="muted">当前访问上下文未同时授予预测读取和创建权限。</p><p v-if="replayError" class="error-text" role="alert">{{ replayError }}</p></section>
      <dl class="facts"><div><dt>色谱类型</dt><dd>{{ detail.chromatography_type.toUpperCase() }}</dd></div><div><dt>来源</dt><dd>{{ detail.source }}</dd></div><div><dt>记录状态</dt><dd>{{ detail.status }}</dd></div><div><dt>创建时间</dt><dd>{{ detail.created_at || '服务端未返回' }}</dd></div><div><dt>revision</dt><dd>{{ detail.revision }}</dd></div><div><dt>ETag</dt><dd>{{ detail.etag || '服务端未返回' }}</dd></div></dl>
      <section class="detail-block"><h3>结构快照</h3><p v-if="detailStructureRefs.length" class="detail-value">结构引用：{{ detailStructureRefs.join('、') }}</p><p v-for="(structure, index) in detailStructures" :key="index" class="detail-value">{{ detailStructures.length > 1 ? `结构 ${index + 1}` : '规范化 SMILES' }}：<code>{{ structure }}</code></p><p v-if="!detailStructureRefs.length && !detailStructures.length" class="muted">服务端未返回结构快照。</p></section>
      <section class="detail-block"><h3>实验条件</h3><dl v-if="detailInputRows.length" class="facts"> <div v-for="row in detailInputRows" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div></dl><p v-else class="muted">服务端未返回可展示的合同条件字段。</p></section>
      <section class="detail-block"><h3>模型溯源</h3><dl class="facts"><div v-for="row in detailModel" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div><div><dt>结果来源</dt><dd>{{ detailPrediction?.data_origin ?? '服务端未返回' }}</dd></div><div><dt>任务 Schema 版本</dt><dd>{{ detailPrediction?.schema_version ?? '服务端未返回' }}</dd></div></dl></section>
    <section v-if="!detail.batch_id" class="detail-block"><h3>正式结果</h3><p v-if="detailPredictionError" class="error-text">关联预测 GET 失败；仅显示记录 GET 已返回的快照，未补写结果。</p><p v-else-if="detailAssociationError" class="error-text">记录与关联预测的 ID 不一致；为避免展示错配结果，未显示关联预测数据。</p><p v-else-if="!detailPrediction?.prediction_record_id" class="muted">记录未关联可读取的预测结果。</p><p v-else-if="!detailResult" class="muted">服务端未返回正式结果数据。</p><template v-else><p v-if="detailPrediction.status !== detail.status" class="warning-text">预测状态 {{ detailPrediction.status }} 与记录状态 {{ detail.status }} 不一致；以各自服务端状态为准。</p><p v-if="detailError" class="error-text">{{ detailError.code }}：{{ detailError.message }}</p><p v-if="detailApplicability.supported_scope" class="detail-value">适用范围：{{ detailApplicability.supported_scope }}</p><p v-if="detailSfcScopePending" class="muted">正在核验候选范围来源…</p><p v-if="detailSfcResultError" class="error-text" data-testid="sfc-result-contract-error">{{ detailSfcResultError }}</p><dl v-if="detailSfcCounts.length" class="facts" data-testid="sfc-record-counts"><div v-for="count in detailSfcCounts" :key="count.label"><dt>{{ count.label }}</dt><dd>{{ count.value }}</dd></div></dl><p v-if="detailSfcCounts.length" class="muted">目录标记只表示条件组合是否已在目录中出现，不代表当前分子的实验数据。</p><dl v-if="detailResult.task_type === 'tlc_reverse_solvent'" class="facts"><div><dt>已评估</dt><dd>{{ detailResult.evaluated }}</dd></div><div><dt>失败</dt><dd>{{ detailResult.failed }}</dd></div><div><dt>目标可达</dt><dd>{{ detailResult.target_reachable ? '是' : '否' }}</dd></div></dl><div v-if="!detailCcResultError && !detailGcResultError && !detailSfcResultError && !detailSfcScopePending && detailResultRows.length" class="table-wrap" role="region" aria-label="正式结果表格，可横向滚动" tabindex="0"><table><thead><tr><th v-for="field in detailResultFields" :key="field">{{ resultFieldLabel(field) }}</th></tr></thead><tbody><tr v-for="(row, index) in detailResultRows" :key="index"><td v-for="field in detailResultFields" :key="field">{{ resultCell(row[field], field) }}</td></tr></tbody></table></div><p v-if="detailCcResultError" class="error-text" data-testid="cc-result-contract-error">CC 结果行不符合冻结合同；已隐藏结果。</p><p v-if="detailGcResultError" class="error-text" data-testid="gc-result-contract-error">{{ detailGcResultError }} 已隐藏结果。</p><p v-if="!detailCcResultError && !detailGcResultError && !detailSfcResultError && !detailSfcScopePending && !detailResultRows.length" class="muted">正式结果未返回结果行。</p><section v-if="showHplcMetrics" class="hplc-metrics"><h4>HPLC 服务端指标</h4><p class="muted">原样展示服务端正式指标及其来源，不在此重算或判断模型一致性。</p><div v-if="detailMetricRows.length" class="metrics-table-wrap" role="region" aria-label="HPLC 指标表格，可横向滚动" tabindex="0" data-testid="hplc-metrics-scroll"><table><thead><tr><th>指标</th><th>Key</th><th>值</th><th>单位</th><th>来源</th></tr></thead><tbody><tr v-for="metric in detailMetricRows" :key="metric.key"><td>{{ metric.label }}</td><td><code>{{ metric.key }}</code></td><td>{{ metric.value }}</td><td>{{ metric.unit }}</td><td>{{ metric.origin }}</td></tr></tbody></table></div><p v-else class="muted" data-testid="hplc-metrics-empty">服务端未返回 HPLC 指标。</p></section></template></section>
      <section v-if="detail.batch_id" class="detail-block" data-testid="batch-record-result"><h3>批量结果</h3><p>{{ batchDetailMessage }}</p><button v-if="batchDetailLocation" type="button" class="secondary" data-testid="open-batch-detail" @click="router.push(batchDetailLocation)">查看批量详情</button></section><section class="detail-block"><h3>警告</h3><p v-if="detailResultWarnings.error" class="error-text" data-testid="record-result-warning-contract-error">{{ detailResultWarnings.error }}</p><ul v-if="detailWarnings.length"><li v-for="(warning, index) in detailWarnings" :key="`${warning.code}-${index}`"><strong v-if="warning.code">{{ warning.code }}：</strong>{{ warning.message }}</li></ul><p v-else-if="!detailResultWarnings.error && (detailPredictionError || detailAssociationError)" class="muted">记录未返回 warning；关联预测警告当前不可确认。</p><p v-else-if="!detailResultWarnings.error" class="muted">服务端未返回 warning。</p></section>
      </template>
    </section>
    <template v-if="selected && selectedRef && !retryIntent">
      <ConfirmationCard :resource-ref="selectedRef" action="resource.archive" />
      <section class="panel"><p class="muted">归档必须先完成服务端预览与人工确认，再绑定当前记录 ETag 提交。</p><button class="primary" :disabled="archiving || !confirmationMatches" @click="archiveSelected">{{ archiving ? '归档中…' : '提交归档' }}</button><p v-if="archiveError" class="error-text">{{ archiveError }}</p></section>
    </template>
    <RouterLink v-if="router.hasRoute('datasets')" class="secondary" to="/data/datasets">打开数据域与导入</RouterLink>
  </div>
</template>

<style scoped>
.records-view,.records-view>.panel,.record-detail,.detail-block,.export-workspace,.hplc-metrics{min-width:0}.records-table-wrap,.table-wrap,.metrics-table-wrap{width:100%;min-width:0;max-width:100%;overflow-x:auto;overscroll-behavior-x:contain}.records-table-wrap table{width:100%;min-width:500px;border-collapse:collapse}.records-table-wrap th,.records-table-wrap td{padding:9px 11px;border-bottom:1px solid #e4edf3;text-align:left;white-space:nowrap}.records-table-wrap td:nth-child(2){min-width:140px;max-width:240px;white-space:normal;overflow-wrap:anywhere}.record-detail{display:grid;gap:18px}.record-detail h2{overflow-wrap:anywhere}.detail-block{border-top:1px solid #e4edf3;padding-top:14px}.detail-block h3{margin:0 0 8px;color:#315d80;font-size:14px}.detail-value{margin:7px 0;color:#274967;font-size:13px;line-height:1.6;overflow-wrap:anywhere}.detail-value code{white-space:pre-wrap;overflow-wrap:anywhere}.table-wrap table,.metrics-table-wrap table{width:max-content;min-width:100%;border-collapse:collapse}.table-wrap th,.table-wrap td,.metrics-table-wrap th,.metrics-table-wrap td{padding:9px 11px;border-bottom:1px solid #e4edf3;text-align:left;overflow-wrap:anywhere}.table-wrap th,.metrics-table-wrap th{color:#587289;font-size:12px;font-weight:650}.table-wrap td,.metrics-table-wrap td{color:#274967;font-size:13px}.metrics-table-wrap th,.metrics-table-wrap td{white-space:nowrap}.metrics-table-wrap code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.hplc-metrics h4{margin:14px 0 6px;color:#315d80;font-size:14px}.detail-block ul{display:grid;gap:7px;margin:0;padding-left:20px;color:#7b5b22;font-size:13px;line-height:1.6}.detail-block li{min-width:0;overflow-wrap:anywhere}.detail-block li strong{overflow-wrap:anywhere}.warning-text{margin:8px 0;color:#9a5a20;font-size:13px}.export-workspace{border-top:1px solid #e4edf3;margin-top:18px;padding-top:16px}.export-workspace h2{margin:0 0 6px;color:#315d80;font-size:17px}.export-workspace h3{margin:14px 0 8px;color:#315d80;font-size:14px}.export-options{display:flex;flex-wrap:wrap;align-items:center;gap:12px;margin:12px 0}.export-options label{display:flex;align-items:center;gap:7px}.export-options select{max-width:100%;border:1px solid #c8d8e5;border-radius:8px;padding:7px;background:#fff}.export-status{min-width:0;border-radius:10px;background:#f5f9fc;padding:12px;overflow-wrap:anywhere}.export-status p{overflow-wrap:anywhere}
</style>
