<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { activeProfile } from '@/app/generated/profile'
import StructureInput from '@/components/StructureInput.vue'
import PredictionHighlights from './PredictionHighlights.vue'
import type { ContractPrediction, ContractPredictionCreateBody, ContractSfcCandidateScopePolicy, ContractSfcCandidateScopePreview, ContractSfcCandidateScopePreviewBody, ContractSfcCandidateScopeProvenance, ContractTlcConditionComparisonBody, ContractTlcConditionOptions } from '@/core/contract-types'
import type { Structure } from '@/core/types'
import { useAppStore } from '@/stores/app'
import { projectSelectionState, resolveProjectId } from '@/core/project-context'
import BatchUnavailable from './forms/basic/BatchUnavailable.vue'
import CcSingleForm from './forms/basic/CcSingleForm.vue'
import GcSingleForm from './forms/basic/GcSingleForm.vue'
import HplcSingleForm from './forms/basic/HplcSingleForm.vue'
import OfflinePredictionForm from './forms/basic/OfflinePredictionForm.vue'
import { ccFormCapabilityFromSchema, ccFormValueError, hplcColumnOptions, type CcFormValue, type GcFormValue, type HplcFormValue } from './forms/basic/form-types'
import { gcFormCapabilityFromSchema, gcFormValueError, type GcFormDraftValue } from './forms/basic/gc-temperature-program'
import TlcToolPanel from '@/shared/tlc/TlcToolPanel.vue'
import type { TlcToolPanelWorkspaceDraft } from '@/shared/tlc/TlcToolPanel.vue'
import { isTlcSolventId, mapTlcReverseRange, type TlcInput, type TlcTool, type TlcReverseRange, type TlcSolventOption, type TlcCandidateSystem } from '@/shared/tlc/types'
import SfcSearchForm from './forms/sfc/SfcSearchForm.vue'
import type { SfcSearchWorkspaceDraft } from './forms/sfc/SfcSearchForm.vue'
import type { SfcSearchBindingOptions, SfcSearchExpansionMode, SfcSearchFormValue, SfcSearchModelChoice, SfcSearchToolChoice } from '@/shared/prediction/sfc-search.types'
import StructureWorkspace from '@/components/StructureWorkspace.vue'
import SharedKetcherCanvas from '@/components/SharedKetcherCanvas.vue'
import { createStructureWorkspaceContext, type StructureWorkspaceContext } from '@/core/structure/workspace'
import { gcQuantileDisplayRows } from '@/shared/prediction/gc-quantiles'
import { unavailableCapabilityReason, usePredictionStore } from './store'
import { gcPredictionModelSnapshotError, isPredictionTerminalStatus, mapTlcReverseCurve, resultMetadataError, toDisplayRow, type DisplayRow, type PredictionResultPayload } from './result'
import { formatResultCell } from '@/shared/prediction/result'
import { areDistinctStructureRefs } from './input-validation'
import { statusLabel } from '@/core/status-labels'
import { ApiClientError } from '@/core/api/client'
import { clearReplayPointer, getReplayDraft, readReplayPointer, ReplayIdentityError, saveReplayPointer, type ReplayDraft, type ReplayInput } from '@/shared/prediction/replay'
import { CandidateModelSelectionError, createExactModelSelection, exactModelIdentityMatches, type PredictionCandidateExactModelSelection, type PredictionCandidateModelOption } from '@/shared/prediction/model-selection'
import { createHplc11Selection, isHplc11HistoricalInput, isHplc11Input, isHplc11RawPair, validHplc11StructureRefs, type Hplc11CreateBody, type Hplc11Input, type Hplc11ModelOption, type Hplc11Prediction } from '@/shared/prediction/hplc11'
import { createGc11Selection, isGc11DraftSelection, isGc11Input, isGc11StructureRefs, type Gc11CreateBody, type Gc11ExactProfileSelection, type Gc11ModelOption, type Gc11Prediction } from '@/shared/prediction/gc11'
import { createSfc11ExactSelection, mapSfcModelOption, mapSfcToolChoice, optionsForSfcSearch, sfcOptionsStayWithinAxes, sfcScopeMatchesSelection, sfcSearchLimitsFromSchema, type Sfc11ModelOption, type Sfc11SingleBody, type Sfc11FullBody, type Sfc11Scope, type Sfc11ScopeCreate } from '@/shared/prediction/sfc11'
import { isSfcScopeProvenance, sameSfcScopeProvenance } from '@/shared/prediction/sfc-provenance'
import { toSfcDisplayRow } from '@/shared/prediction/result'
import { emptyPredictionWorkspace, predictionWorkspaceKey, readPredictionWorkspace, writePredictionWorkspace, type PredictionWorkspace } from '@/shared/prediction/workspace'

const app = useAppStore()
const store = usePredictionStore()
const structureWorkspace: StructureWorkspaceContext = createStructureWorkspaceContext()
const route = useRoute()
const router = useRouter()
const activeMode = ref<'single' | 'batch'>('single')
const structures = ref<(Structure | null)[]>([])
type HplcRawStructureBinding = { rawSmiles: string; structureId: string; projectId: string; generation: number }
const hplcRawStructures = ref<(HplcRawStructureBinding | null)[]>([])
const structureInputGeneration = ref(0)
const sfcSearchTools = ref<SfcSearchToolChoice[]>([])
const sfcSearchModels = ref<SfcSearchModelChoice[]>([])
const sfcSelectedToolId = ref<string>(route.query.tool === 'single_column' || route.query.tool === 'full_column' ? route.query.tool : '')
const sfcSearchOptions = ref<SfcSearchBindingOptions | null>(null)
const sfcOptionsState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const sfcOptionsError = ref<string | null>(null)
const sfcCandidateError = ref<string | null>(null)
const sfcScopeOptionsError = ref<string | null>(null)
const sfcPredictionError = ref<string | null>(null)
const sfcPredictionStatusError = ref<string | null>(null)
const sfcSubmissionLocked = ref(false)
const sfcPreview = ref<ContractSfcCandidateScopePreview | null>(null)
const sfcPreviewBusy = ref(false)
const sfcBoundScopeCount = ref<{ scopeId: string; count: number } | null>(null)
let sfcPreviewGeneration = 0
let sfcCandidateGeneration = 0
let preparedSfcScope: { key: string; scope: Sfc11Scope } | null = null
let reloadSfcScope: (() => Promise<void>) | null = null
let sfcStatusRecovery: { id: string; binding: SfcFollowBinding } | null = null
const sfcAcceptedId = ref<string | null>(null)
const sfcPreviewText = computed(() => {
  const preview = sfcPreview.value
  if (!preview) return null
  const split = preview.expansion_mode === 'coupled_discrete_conditions'
    ? `其中条件目录中已有 ${preview.observed_count ?? '未知'} 条，组合补充 ${preview.unobserved_count ?? '未知'} 条。` : ''
  return `已有完整条件组合：匹配 ${preview.matched_count} 条，有效上限 ${preview.max_candidates} 条。${split}${preview.status === 'EMPTY' ? '当前组合无匹配，请调整条件。' : preview.status === 'OVER_LIMIT' ? '超过上限，请缩小条件范围。' : '范围可用；正式提交时将再次校验。'}`
})
let sfcSelectionGeneration = 0
let sfcOptionsRequestGeneration = 0
const selectedModelKey = ref('')
const selectionContext = ref({ type: '', projectId: '', modelKey: '' })
const selectionInvalidated = ref(false)
const formError = ref<string | null>(null)
const submissionLocked = ref(false)
const replayDraft = ref<ReplayDraft | null>(null)
const replayLoading = ref(false)
const replayRequested = ref(false)
const formDraft = ref<unknown>(null)
const restoredSmiles = ref<string[]>([])
const tlcWorkspaceDraft = ref<TlcToolPanelWorkspaceDraft | null>(null)
const sfcWorkspaceDraft = ref<SfcSearchWorkspaceDraft | null>(null)
let activeWorkspace: { key: string; actor: string; project: string; type: string; tool: string } | null = null
let workspaceValue: PredictionWorkspace | null = null
let workspaceGeneration = 0
let structureDraftRestoreGeneration = 0
const submitting = ref(false)
const pollTimer = ref<number | null>(null)
let projectGeneration = 0
let submitAttemptGeneration = 0
let loadRequestGeneration = 0
let tlcToolLoadGeneration = 0
let viewInitialized = false
let typesRequestGeneration = 0
let retryMetadataPending = app.state === 'error'

const selectedProjectId = ref('')
const availableProjects = computed(() => app.accessContext?.accessible_projects ?? [])
const projectId = computed(() => resolveProjectId(app.accessContext, selectedProjectId.value) ?? '')
const actorId = computed(() => app.accessContext?.actor_id ?? '')
const workspaceToolId = computed(() => store.selectedType === 'sfc'
  ? (['single_column', 'full_column'].includes(sfcSelectedToolId.value) ? sfcSelectedToolId.value
    : ['single_column', 'full_column'].includes(store.selectedTool) ? store.selectedTool : 'full_column')
  : store.selectedType === 'tlc'
    ? (['single', 'batch', 'condition_compare', 'reverse_solvent'].includes(String(route.query.tool))
      ? String(route.query.tool) : ['single', 'batch', 'condition_compare'].includes(store.selectedTool) ? store.selectedTool : 'single')
    : (activeMode.value === 'batch' ? 'batch' : 'single'))
const projectState = computed(() => projectSelectionState(app.accessContext, selectedProjectId.value))
const profileTypeLabels: Record<string, string> = { tlc: '薄层色谱', cc: '柱层析', hplc: '手性 HPLC', gc: '气相色谱', sfc: '超临界流体色谱' }
const offlinePreview = computed(() => !projectId.value || store.state === 'error' || (store.state === 'loading' && store.typeCapability?.id !== store.selectedType))
const serviceActionsUnavailable = computed(() => app.state !== 'ready')
const allowedActions = computed(() => app.accessContext?.allowed_actions ?? [])
const canReadPredictions = computed(() => allowedActions.value.includes('*') || allowedActions.value.includes('prediction:read'))
const canCreatePredictions = computed(() => allowedActions.value.includes('*') || allowedActions.value.includes('prediction:create'))
const projectContextKey = computed(() => {
  const context = app.accessContext
  if (!context) return ''
  return JSON.stringify([context.actor_id, context.actor?.policy_version, [...availableProjects.value.map((item) => item.id)].sort(), context.default_project_id, [...(context.permissions ?? [])].sort(), [...allowedActions.value].sort()])
})
const projectContextPending = computed(() => !app.accessContext && app.state !== 'error')
const projectContextFailed = computed(() => !app.accessContext && app.state === 'error')
const typeOrder = ['tlc', 'cc', 'hplc', 'gc', 'sfc']
const visibleTypes = computed(() => activeProfile.chromatographyTypes.filter((id) => typeOrder.includes(id)).sort((a, b) => typeOrder.indexOf(a) - typeOrder.indexOf(b)).map((id) => ({ id, display_name: store.types.find((item) => item.id === id)?.display_name ?? profileTypeLabels[id] ?? id.toUpperCase() })))
const visibleTlcTools = computed(() => {
  if (!activeProfile.modules.includes('prediction') || !activeProfile.chromatographyTypes.includes('tlc')) return []
  const configured = (activeProfile.predictionTools.tlc ?? []).filter((tool): tool is TlcTool => ['single', 'batch', 'condition_compare', 'reverse_solvent'].includes(tool))
  const available = new Set<TlcTool>([...configured, 'batch'])
  return (['single', 'batch', 'condition_compare', 'reverse_solvent'] as const).filter((tool) => available.has(tool) && (tool === 'batch' || configured.includes(tool)))
})
const routeType = computed(() => typeof route.meta.chromatographicType === 'string' ? route.meta.chromatographicType : '')
const selectedSummary = computed(() => store.types.find((item) => item.id === store.selectedType) ?? null)
const selectedHealth = computed(() => store.typeCapability?.health ?? selectedSummary.value?.health ?? null)
const healthyModels = computed(() => store.typeCapability?.models.filter((model) => model.health.status === 'READY') ?? [])
function modelOptionKey(model: { id: string; version: string; preprocessing_profile_id?: string | null; preprocessing_profile_sha256?: string | null }): string {
  return JSON.stringify([model.id, model.version, model.preprocessing_profile_id ?? null, model.preprocessing_profile_sha256 ?? null])
}
const selectedModel = computed(() => healthyModels.value.find((model) => modelOptionKey(model) === selectedModelKey.value) ?? null)
const gcFormCapability = computed(() => store.selectedType === 'gc'
  ? gcFormCapabilityFromSchema(store.toolSchema?.input_schema, store.toolSchema?.model_constraints, selectedModel.value ? { id: selectedModel.value.id, scope: selectedModel.value.scope } : null)
  : null)
const gcCapabilityReady = computed(() => {
  if (store.selectedType !== 'gc') return true
  const type = store.typeCapability
  const tool = type?.tools.find((item) => item.id === 'single')
  const schema = store.toolSchema
  const model = selectedModel.value
  if (type?.id !== 'gc' || type.health.status !== 'READY' || !tool || tool.health.status !== 'READY'
    || tool.schema_version !== '1.1' || !schema || schema.id !== 'single' || schema.schema_version !== '1.1'
    || schema.health.status !== 'READY' || !model || model.input_schema_version !== '1.1' || !gcFormCapability.value) return false
  try { createGc11Selection(model as Gc11ModelOption); return true }
  catch { return false }
})
const selectedUnavailable = computed(() => selectedHealth.value?.status === 'UNAVAILABLE'
  || (store.selectedType === 'sfc' && store.typeCapability !== null && selectedHealth.value?.status !== 'READY')
  || (store.selectedType === 'gc' && store.typeCapability !== null && !gcCapabilityReady.value))
const selectedUnavailableReason = computed(() => selectedHealth.value?.status === 'UNAVAILABLE'
  ? unavailableCapabilityReason(selectedHealth.value)
  : store.selectedType === 'sfc' && selectedHealth.value?.status !== 'READY'
    ? selectedHealth.value?.reason ?? '能力状态尚未 READY'
    : store.selectedType === 'gc' && store.typeCapability !== null && !gcCapabilityReady.value
      ? 'GC 1.1 模型、预处理版本或完整 READY 输入 schema 尚未匹配；提交已禁用。'
      : null)
const replayModelMatches = computed(() => !replayRequested.value || Boolean(replayDraft.value && selectedModel.value
  && exactModelIdentityMatches(replayDraft.value.model_selection, selectedModel.value as PredictionCandidateModelOption)))
const selectedModelBinding = computed(() => {
  const model = selectedModel.value
  if (!model) return { selection: null, error: null }
  try {
    const selection = store.selectedType === 'hplc'
      ? createHplc11Selection(model as Hplc11ModelOption)
      : store.selectedType === 'gc'
        ? createGc11Selection(model as Gc11ModelOption)
        : createExactModelSelection(model as PredictionCandidateModelOption, store.selectedType)
    return { selection, error: null }
  }
  catch (reason) { return { selection: null, error: reason instanceof Error ? reason.message : '当前模型身份无效；预测已禁用。' } }
})
const requiredStructures = computed(() => ['cc', 'hplc', 'sfc'].includes(store.selectedType) ? 2 : 1)
const requiredStructureTargetIds = computed(() => requiredStructures.value === 2 ? ['primary', 'secondary'] : ['primary'])
const rawStructureInputsReady = computed(() => requiredStructureTargetIds.value.every((id) => {
  const target = structureWorkspace.targets.get(id)
  return Boolean(target && target.projectId === projectId.value && target.getSmiles().trim())
}))
const replayStructureIds = computed(() => replayDraft.value?.structure_refs.map((item) => item.id) ?? [])
const replayStructureSnapshots = computed(() => replayDraft.value?.structure_snapshots ?? [])
const structureReady = computed(() => replayDraft.value
  ? replayDraft.value.project_id === projectId.value && replayStructureIds.value.length === requiredStructures.value && replayStructureIds.value.every(Boolean) && (store.selectedType !== 'cc' || areDistinctStructureRefs(replayStructureIds.value))
  : rawStructureInputsReady.value)
const hplcReplayRawPair = computed(() => replayDraft.value?.chromatography_type === 'hplc'
  && replayDraft.value.schema_version === '1.1'
  && isHplc11Input(replayDraft.value.input)
  ? replayDraft.value.input.model_input_smiles : null)
const hplcRawStructuresReady = computed(() => store.selectedType === 'hplc' && (replayDraft.value
  ? replayDraft.value.schema_version === '1.1' && isHplc11RawPair(hplcReplayRawPair.value) && replayDraft.value.structure_refs.length === 2
  : Array.from({ length: 2 }, (_, index) => {
  const binding = hplcRawStructures.value[index]
  const structure = structures.value[index]
  return Boolean(binding && structure && binding.projectId === projectId.value && binding.generation === structureInputGeneration.value && binding.structureId === structure.id && structure.project_id === projectId.value)
  }).every(Boolean)))
const replayInput = computed(() => replayDraft.value?.input ?? null)
const replayUiSupported = computed(() => {
  const draft = replayDraft.value
  if (!draft || !['cc', 'hplc', 'gc', 'tlc'].includes(draft.chromatography_type) || draft.tool !== 'single') return false
  if (draft.chromatography_type === 'gc') return draft.schema_version === '1.1'
    && isGc11Input(draft.input) && isGc11DraftSelection(draft.model_selection)
  if (draft.chromatography_type !== 'hplc') return true
  return draft.schema_version === '1.1' && isHplc11Input(draft.input)
    && typeof draft.model_selection.preprocessing_profile_id === 'string'
    && typeof draft.model_selection.preprocessing_profile_sha256 === 'string'
})
const hplcReplayInput = computed(() => {
  const input = replayDraft.value?.chromatography_type === 'hplc' ? replayDraft.value.input : null
  if (isHplc11Input(input)) return { version: '1.1' as const, input }
  if (isHplc11HistoricalInput(input)) return { version: '1.0' as const, input }
  return null
})
const inputSchema = computed(() => store.toolSchema?.input_schema)
const ccFormCapability = computed(() => store.selectedType === 'cc'
  ? ccFormCapabilityFromSchema(store.toolSchema?.input_schema, store.toolSchema?.model_constraints)
  : null)
const hplcColumns = computed(() => hplcColumnOptions(store.toolSchema?.model_constraints))
const selectedTypeLabel = computed(() => store.types.find((item) => item.id === store.selectedType)?.display_name ?? store.selectedType.toUpperCase())
const resultPayload = computed<PredictionResultPayload | null | undefined>(() => store.prediction?.result)
const tlcReverseCurve = computed(() => mapTlcReverseCurve(resultPayload.value, store.prediction?.input_snapshot?.input, store.prediction?.status, Boolean(store.accepted && !store.prediction)))
const resultRows = computed<DisplayRow[]>(() => {
  const result = resultPayload.value
  if (!result) return []
  if (result.task_type === 'tlc_condition_comparison') return result.results.map((row) => toDisplayRow(row))
  if (result.task_type === 'tlc_reverse_solvent') return result.candidates.map((row) => toDisplayRow(row))
  if (!Array.isArray(result.rows)) return []
  if (result.task_type === 'gc') return result.rows.flatMap((row) => {
    const source = row as Record<string, unknown>
    const quantiles = gcQuantileDisplayRows(source.quantiles) ?? []
    return [
      { metric: '预测保留时间', value: source.retention_time_min as number, unit: 'min' },
      ...quantiles,
    ]
  })
  if (result.task_type === 'sfc') return result.rows.map((row) => toSfcDisplayRow(row))
  return result.rows.map((row) => toDisplayRow(row))
})
const resultFields = computed(() => [...new Set(resultRows.value.flatMap((row) => Object.keys(row)))]
  .filter((field) => store.prediction?.chromatography_type !== 'sfc' || field !== 'score_semantics'))
const resultWarnings = computed(() => resultPayload.value?.task_type === 'tlc_reverse_solvent' ? resultPayload.value.warnings : [])
const hplcPrediction = computed(() => store.prediction?.chromatography_type === 'hplc' ? store.prediction as unknown as Hplc11Prediction : null)
const hplcPredictionInput = computed(() => {
  const input = hplcPrediction.value?.input_snapshot.input
  if (isHplc11Input(input)) return { version: '1.1' as const, input }
  if (isHplc11HistoricalInput(input)) return { version: '1.0' as const, input }
  return null
})
const hplcApplicabilityWarnings = computed(() => {
  const prediction = hplcPrediction.value
  const result = prediction?.result
  if (!prediction || prediction.status !== 'SUCCEEDED' || resultError.value || !result || result.task_type !== 'hplc') return []
  const warnings = result.applicability?.warnings
  if (!Array.isArray(warnings)) return []
  return warnings.flatMap((warning) => {
    if (!warning || typeof warning !== 'object' || Array.isArray(warning)) return []
    const item = warning as Record<string, unknown>
    if (typeof item.code !== 'string' || !item.code.trim() || item.code.length > 96
      || typeof item.message !== 'string' || !item.message.trim() || item.message.length > 2000) return []
    return [{ code: item.code, message: item.message }]
  })
})
const sfcApplicabilityWarnings = computed(() => {
  const prediction = store.prediction
  const result = prediction?.result
  if (!prediction || prediction.chromatography_type !== 'sfc' || prediction.status !== 'SUCCEEDED'
    || resultError.value || !result || result.task_type !== 'sfc') return []
  const warnings = result.applicability?.warnings
  if (!Array.isArray(warnings)) return []
  return warnings.flatMap((warning) => {
    if (!warning || typeof warning !== 'object' || Array.isArray(warning)) return []
    const item = warning as Record<string, unknown>
    if (typeof item.code !== 'string' || !item.code.trim() || item.code.length > 96
      || typeof item.message !== 'string' || !item.message.trim() || item.message.length > 2000) return []
    return [{ code: item.code, message: item.message }]
  })
})
const sfcSupportedScope = computed(() => {
  const prediction = store.prediction
  const result = prediction?.result
  if (!prediction || prediction.chromatography_type !== 'sfc' || prediction.status !== 'SUCCEEDED'
    || resultError.value || !result || result.task_type !== 'sfc') return null
  const scope = result.applicability?.supported_scope
  return typeof scope === 'string' && scope.trim().length > 0 && scope.length <= 500 ? scope : null
})
const sfcEmptyResult = computed(() => {
  const prediction = store.prediction
  const result = prediction?.result
  return Boolean(prediction && prediction.chromatography_type === 'sfc' && prediction.status === 'SUCCEEDED'
    && !resultError.value && result?.task_type === 'sfc' && Array.isArray(result.rows) && result.rows.length === 0)
})
const sfcResultCounts = computed(() => {
  const prediction = store.prediction
  const result = prediction?.result
  if (!prediction || prediction.chromatography_type !== 'sfc' || prediction.status !== 'SUCCEEDED'
    || resultError.value || !result || result.task_type !== 'sfc' || !Array.isArray(result.rows)) return null
  const provenance = result.candidate_scope_provenance
  const scopeId = (prediction.input_snapshot.input as unknown as Record<string, unknown>).candidate_scope_id
  const frozenScopeCount = isSfcScopeProvenance(provenance) ? provenance.candidate_count
    : typeof scopeId === 'string' && sfcBoundScopeCount.value?.scopeId === scopeId ? sfcBoundScopeCount.value.count : null
  const evaluated = result.evaluated_candidate_count
  const evaluatedCount = typeof evaluated === 'number' && Number.isSafeInteger(evaluated) && evaluated >= 1 && evaluated <= 100_000 ? evaluated : null
  const input = prediction.input_snapshot.input as unknown as Record<string, unknown>
  const topK = typeof input.top_k === 'number' && Number.isSafeInteger(input.top_k) && input.top_k >= 1 ? input.top_k : null
  return { frozenScopeCount, evaluatedCount, returnedCount: result.rows.length, topK }
})
const gcApplicabilityWarnings = computed(() => {
  const prediction = store.prediction
  const result = resultPayload.value
  if (!prediction || prediction.status !== 'SUCCEEDED' || resultError.value || !result || result.task_type !== 'gc') return []
  const warnings = result.applicability?.warnings
  if (!Array.isArray(warnings)) return []
  const topLevelMessages = new Set((prediction.warnings ?? []).map((warning) => warning.message))
  return warnings.flatMap((warning) => {
    if (!warning || typeof warning !== 'object' || Array.isArray(warning)) return []
    const item = warning as Record<string, unknown>
    if (typeof item.code !== 'string' || !item.code.trim() || item.code.length > 96
      || typeof item.message !== 'string' || !item.message.trim() || item.message.length > 2000
      || topLevelMessages.has(item.message)) return []
    return [{ code: item.code, message: item.message }]
  })
})
const reverseUnreachable = computed(() => resultPayload.value?.task_type === 'tlc_reverse_solvent' && resultPayload.value.target_reachable === false)
const ccResultReady = computed(() => store.prediction?.chromatography_type === 'cc'
  && store.prediction.status === 'SUCCEEDED' && !resultError.value && resultPayload.value?.task_type === 'cc')
type CcMetricRow = { key: string; label: string; value: unknown; unit: string; origin: string }
const ccMetricRows = computed<CcMetricRow[]>(() => {
  if (!ccResultReady.value || resultPayload.value?.task_type !== 'cc') return []
  const metrics = resultPayload.value.metrics
  if (!Array.isArray(metrics)) return []
  return metrics.flatMap((metric) => {
    if (!metric || typeof metric !== 'object' || Array.isArray(metric)) return []
    const item = metric as Record<string, unknown>
    const keys = Object.keys(item)
    if (keys.some((key) => !['key', 'label', 'value', 'unit', 'origin'].includes(key))
      || !['key', 'label', 'value', 'origin'].every((key) => key in item)
      || typeof item.key !== 'string' || !item.key.trim() || item.key.length > 96
      || typeof item.label !== 'string' || !item.label.trim() || item.label.length > 200
      || (typeof item.value !== 'number' && typeof item.value !== 'string' && item.value !== null)
      || (typeof item.value === 'number' && !Number.isFinite(item.value))
      || (item.unit !== undefined && item.unit !== null && (typeof item.unit !== 'string' || item.unit.length > 32))
      || (item.origin !== 'model' && item.origin !== 'derived')) return []
    return [{
      key: item.key,
      label: item.label,
      value: item.value,
      unit: typeof item.unit === 'string' && item.unit ? item.unit : '服务端未声明',
      origin: item.origin === 'derived' ? '服务端派生' : '模型返回',
    }]
  })
})
const ccMetricDataInvalid = computed(() => ccResultReady.value && resultPayload.value?.task_type === 'cc'
  && Array.isArray(resultPayload.value.metrics) && ccMetricRows.value.length !== resultPayload.value.metrics.length)
type CcApplicabilityWarning = { code: string; message: string }
const ccApplicabilityWarnings = computed<CcApplicabilityWarning[]>(() => {
  if (!ccResultReady.value || resultPayload.value?.task_type !== 'cc') return []
  const warnings = resultPayload.value.applicability?.warnings
  if (!Array.isArray(warnings)) return []
  return warnings.flatMap((warning) => {
    if (!warning || typeof warning !== 'object' || Array.isArray(warning)) return []
    const item = warning as Record<string, unknown>
    if (typeof item.code !== 'string' || !item.code.trim() || item.code.length > 96
      || typeof item.message !== 'string' || !item.message.trim() || item.message.length > 2000) return []
    return [{ code: item.code, message: item.message }]
  })
})
const ccResultLabels: Record<string, string> = {
  volume_ml: '体积（mL，模型返回）',
  flow_rate_ml_min: '流速（mL/min）',
  time_seconds: '时间（s，服务端派生）',
}
function resultFieldLabel(field: string): string {
  if (store.prediction?.chromatography_type === 'cc') return ccResultLabels[field] ?? field
  if (store.prediction?.chromatography_type === 'sfc') {
    const sfcLabels: Record<string, string> = {
      candidate_id: '候选编号', rank: '排名', column: '色谱柱', stationary_phase: '固定相', mode: '模式',
      length_mm: '柱长（mm）', inner_diameter_mm: '内径（mm）', particle_size_um: '填料粒径（µm）',
      column_temperature_c: '柱温（°C）', flow_rate_ml_min: '流速（mL/min）', observed_in_source: '条件目录中已有',
    }
    if (field === 'separation') {
      const threshold = (store.prediction.model as unknown as { threshold?: unknown }).threshold
      if ((typeof threshold === 'string' && threshold.trim().length > 0 && threshold.length <= 32)
        || (typeof threshold === 'number' && Number.isFinite(threshold))) return `P(Rs ≥ ${threshold})`
      return '达标概率（阈值未保存）'
    }
    return sfcLabels[field] ?? field
  }
  if (store.prediction?.chromatography_type === 'gc') {
    if (field === 'retention_time_min') return '保留时间（min）'
    if (field === 'metric') return '指标'
    if (field === 'value') return '保留时间'
    if (field === 'unit') return '单位'
  }
  return field
}
const dynamicSchema = computed<Record<string, unknown>>(() => inputSchema.value ?? {})
const resultError = computed(() => {
  const prediction = store.prediction
  if (!prediction || !isPredictionTerminalStatus(prediction.status)) return null
  const metadataError = resultMetadataError(prediction.result, prediction.chromatography_type)
  if (metadataError) return metadataError
  if (prediction.chromatography_type === 'gc' && prediction.result?.task_type === 'gc'
    && prediction.result.rows.some((row) => gcQuantileDisplayRows((row as Record<string, unknown>).quantiles) === null)) {
    return 'GC 分位数字段不符合 Q10、Q50、Q90 结果展示约定；正式结果已隐藏。'
  }
  if (prediction.chromatography_type === 'hplc' && prediction.result) {
    const outputSchemaVersion = (prediction as unknown as Hplc11Prediction).model?.output_schema_version
    if (typeof outputSchemaVersion !== 'string' || !outputSchemaVersion.trim().length) return 'HPLC 模型缺少输出 schema 身份；正式结果已隐藏。'
    if (prediction.result.schema_version !== outputSchemaVersion) return 'HPLC 结果 schema 与模型声明的输出 schema 不匹配；正式结果已隐藏。'
  }
  if (prediction.chromatography_type === 'gc' && prediction.result) {
    const snapshotError = gcPredictionModelSnapshotError(prediction.model, prediction.schema_version)
    if (snapshotError) return `${snapshotError} 正式结果已隐藏。`
    const outputSchemaVersion = prediction.model?.output_schema_version
    if (typeof outputSchemaVersion !== 'string' || !outputSchemaVersion.trim().length) return 'GC 模型缺少输出 schema 身份；正式结果已隐藏。'
    if (prediction.result.schema_version !== outputSchemaVersion) return 'GC 结果 schema 与模型声明的输出 schema 不匹配；正式结果已隐藏。'
  }
  return null
})
const visiblePredictionWarnings = computed(() => {
  const prediction = store.prediction
  if (!prediction || (['hplc', 'gc'].includes(prediction.chromatography_type) && resultError.value)) return []
  return prediction.warnings ?? []
})
const resultStatusLabel = computed(() => reverseUnreachable.value ? '目标不可达' : (resultError.value ? '结果校验失败' : statusLabel(store.prediction?.status ?? '')))

const sfcCapabilityTools = computed(() => store.typeCapability?.id === 'sfc' ? store.typeCapability.tools.filter((item) => item.id !== 'batch') : [])
const sfcSelectedCapabilityTool = computed(() => {
  const tool = sfcCapabilityTools.value.find((item) => item.id === sfcSelectedToolId.value)
  return tool && 'max_candidates' in tool.limits ? { ...tool, limits: tool.limits } : null
})
const sfcSelectedToolChoice = computed(() => sfcSearchTools.value.find((item) => item.id === sfcSelectedToolId.value) ?? null)
const sfcMappedModels = computed(() => (store.typeCapability?.id === 'sfc' ? store.typeCapability.models : [])
  .map((model) => ({ raw: model as unknown as Sfc11ModelOption, choice: mapSfcModelOption(model as unknown as Sfc11ModelOption) }))
  .filter((item): item is { raw: Sfc11ModelOption; choice: SfcSearchModelChoice } => Boolean(item.choice)))
const sfcSelectedModelBinding = computed(() => sfcMappedModels.value.find((item) => modelOptionKey(item.raw) === selectedModelKey.value) ?? null)
const sfcSelectedModelChoice = computed(() => sfcSelectedModelBinding.value?.choice ?? null)
const sfcSearchLimits = computed(() => sfcSearchLimitsFromSchema(store.toolSchema?.input_schema, store.toolSchema?.limits))
const sfcCapabilityReady = computed(() => Boolean(store.typeCapability?.id === 'sfc'
  && store.typeCapability.health.status === 'READY'
  && sfcSelectedCapabilityTool.value?.health.status === 'READY'
  && sfcSelectedCapabilityTool.value.schema_version === '1.1'
  && store.toolSchema?.id === sfcSelectedToolId.value && store.toolSchema.schema_version === '1.1'
  && store.toolSchema.health.status === 'READY' && sfcSearchLimits.value))
const sfcFormReady = computed(() => Boolean(sfcCapabilityReady.value && sfcSelectedModelBinding.value
  && sfcSelectedModelChoice.value?.health === 'READY' && sfcSearchOptions.value && sfcOptionsState.value === 'ready'
  && sfcSearchOptions.value.projectId === projectId.value && sfcSearchOptions.value.modelId === sfcSelectedModelBinding.value.raw.id
  && sfcSearchOptions.value.modelVersion === sfcSelectedModelBinding.value.raw.version && sfcSearchOptions.value.toolId === sfcSelectedToolId.value))
const sfcFormDisabled = computed(() => serviceActionsUnavailable.value || !canCreatePredictions.value || !projectId.value
  || projectState.value !== 'resolved' || replayLoading.value || Boolean(replayRequested.value))
function isNonNegativeSafeCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0
}
function isSfcPolicy(value: unknown): value is ContractSfcCandidateScopePolicy {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const policy = value as Record<string, unknown>
  return Object.keys(policy).length === 3 && policy.expansion_mode === 'coupled_discrete_conditions'
    && policy.policy_version === '1' && typeof policy.candidate_source_sha256 === 'string'
    && /^[0-9a-f]{64}$/.test(policy.candidate_source_sha256)
}
function sameSfcPolicy(left: unknown, right: unknown): boolean {
  return isSfcPolicy(left) && isSfcPolicy(right)
    && left.expansion_mode === right.expansion_mode && left.policy_version === right.policy_version
    && left.candidate_source_sha256 === right.candidate_source_sha256
}
function previewMatchesExpansion(preview: ContractSfcCandidateScopePreview, mode: SfcSearchExpansionMode, budget: number): boolean {
  if (preview.expansion_mode !== mode || preview.max_candidates !== budget
    || !isNonNegativeSafeCount(preview.matched_count)
    || preview.status !== (preview.matched_count === 0 ? 'EMPTY' : preview.matched_count > budget ? 'OVER_LIMIT' : 'READY')) return false
  const observed = preview.observed_count
  const unobserved = preview.unobserved_count
  if (!isNonNegativeSafeCount(observed) || !isNonNegativeSafeCount(unobserved)
    || observed + unobserved !== preview.matched_count) return false
  return mode === 'coupled_discrete_conditions' ? isSfcPolicy(preview.policy) : preview.policy == null
}
function scopeProvenance(scope: Sfc11Scope, mode: SfcSearchExpansionMode): ContractSfcCandidateScopeProvenance | null {
  if (mode === 'observed_distinct_rows') return null
  const provenance = {
    scope_id: scope.id,
    policy: scope.policy,
    candidate_count: scope.candidate_count,
    observed_count: scope.observed_count,
    unobserved_count: scope.unobserved_count,
  }
  return isSfcScopeProvenance(provenance) ? provenance : null
}
function scopeMatchesExpansion(scope: Sfc11Scope, preview: ContractSfcCandidateScopePreview, mode: SfcSearchExpansionMode): boolean {
  if (scope.expansion_mode !== mode || scope.candidate_count !== preview.matched_count) return false
  if (mode === 'coupled_discrete_conditions') return sameSfcPolicy(scope.policy, preview.policy)
    && scope.observed_count === preview.observed_count && scope.unobserved_count === preview.unobserved_count
    && scopeProvenance(scope, mode) !== null
  if (scope.policy != null) return false
  return isNonNegativeSafeCount(scope.observed_count) && isNonNegativeSafeCount(scope.unobserved_count)
    && scope.observed_count + scope.unobserved_count === scope.candidate_count
    && scope.observed_count === preview.observed_count && scope.unobserved_count === preview.unobserved_count
}
async function validateSfcPredictionBinding(
  prediction: ContractPrediction,
  expectedProjectId: string,
  expectedModel: Sfc11ModelOption,
  expectedToolId: string,
  isCurrent: () => boolean,
  expectedScopeId?: string,
  expectedExpansionMode: SfcSearchExpansionMode = 'observed_distinct_rows',
  expectedProvenance: ContractSfcCandidateScopeProvenance | null = null,
  expectedCandidateCount?: number,
): Promise<boolean> {
  const fail = () => false
  const input = prediction.input_snapshot.input as unknown as Record<string, unknown>
  const scopeId = input.candidate_scope_id
  const model = prediction.model as unknown as Record<string, unknown>
    if (!isCurrent() || (expectedExpansionMode === 'coupled_discrete_conditions' && !expectedProvenance)
      || prediction.project_id !== expectedProjectId || prediction.chromatography_type !== 'sfc'
    || prediction.tool !== expectedToolId || prediction.schema_version !== '1.1'
    || model.id !== expectedModel.id || model.version !== expectedModel.version
    || model.preprocessing_profile_id != null || model.preprocessing_profile_sha256 != null
    || typeof scopeId !== 'string' || !scopeId || (expectedScopeId !== undefined && scopeId !== expectedScopeId)) return fail()
  try {
    const options = await store.loadSfc11Options({ project_id: expectedProjectId, scope_id: scopeId })
    const mappedTool = sfcSearchTools.value.find((item) => item.id === expectedToolId)
    const limits = sfcSearchLimits.value
    const mappedOptions = mappedTool && limits ? optionsForSfcSearch(options.data, expectedModel, mappedTool, expectedProjectId, limits, scopeId) : null
    const hasSfcResult = prediction.result?.task_type === 'sfc'
    const resultProvenance = hasSfcResult && prediction.result && 'candidate_scope_provenance' in prediction.result ? prediction.result.candidate_scope_provenance : null
    const matches = Boolean(isCurrent() && mappedOptions && mappedOptions.supportedExpansionModes?.includes(expectedExpansionMode)
      && (expectedCandidateCount === undefined || options.data.candidate_count === expectedCandidateCount)
      && sameSfcScopeProvenance(mappedOptions.scopeProvenance, expectedProvenance)
      && (!hasSfcResult || sameSfcScopeProvenance(resultProvenance, expectedProvenance))) || fail()
    if (matches && isCurrent()) sfcBoundScopeCount.value = { scopeId, count: options.data.candidate_count }
    return matches
  } catch (reason) { throw reason }
}
type SfcFollowBinding = { model: Sfc11ModelOption; toolId: string; scopeId: string; candidateCount: number; expansionMode: SfcSearchExpansionMode; provenance: ContractSfcCandidateScopeProvenance | null; selectionGeneration: number }
function invalidateSfcCandidates() {
  sfcCandidateGeneration++
  sfcPreviewGeneration++
  sfcPreviewBusy.value = false
  sfcPreview.value = null
  sfcCandidateError.value = null
  sfcScopeOptionsError.value = null
  preparedSfcScope = null
  reloadSfcScope = null
}
watch([projectId, selectedModelKey, sfcSelectedToolId], () => {
  invalidateSfcCandidates()
  sfcPredictionError.value = null
  sfcPredictionStatusError.value = null
  sfcSubmissionLocked.value = false
  sfcStatusRecovery = null
  sfcAcceptedId.value = null
})
async function previewSfcCandidates(
  axes: SfcSearchFormValue['candidateAxes'],
  mode: SfcSearchExpansionMode = 'observed_distinct_rows',
): Promise<ContractSfcCandidateScopePreview | null> {
  if (serviceActionsUnavailable.value) { sfcCandidateError.value = '连接不可用；离线时不会创建或预检候选范围。'; return null }
  const model = sfcSelectedModelBinding.value?.raw
  const tool = sfcSelectedCapabilityTool.value
  const limits = sfcSearchLimits.value
  if (!model || !tool || !limits || !sfcFormReady.value || !canCreatePredictions.value
    || !sfcSearchOptions.value?.supportedExpansionModes?.includes(mode)) return null
  const identity = JSON.stringify([projectId.value, selectedModelKey.value, sfcSelectedToolId.value, mode])
  const generation = ++sfcPreviewGeneration
  const isCurrent = () => generation === sfcPreviewGeneration && identity === JSON.stringify([projectId.value, selectedModelKey.value, sfcSelectedToolId.value, mode])
  sfcPreviewBusy.value = true; sfcPreview.value = null; sfcCandidateError.value = null
  try {
    const budget = Math.min(limits.candidateLimit, tool.limits.max_candidates, tool.limits.max_evaluations)
    const body: ContractSfcCandidateScopePreviewBody = {
      project_id: projectId.value, axes, max_candidates: budget, model_selection: createSfc11ExactSelection(model), expansion_mode: mode,
    }
    const { data } = await store.previewSfc11CandidateScope(body)
    if (!isCurrent()) return null
    if (data.project_id !== projectId.value || data.model_id !== model.id || data.model_version !== model.version
      || !previewMatchesExpansion(data, mode, budget)) {
      throw new Error('候选预检返回与当前项目、模型或预算不匹配。')
    }
    sfcPreview.value = data
    return data
  } catch (reason) {
    if (isCurrent()) sfcCandidateError.value = reason instanceof Error ? reason.message : '候选预检读取失败，请重新检查范围。'
    return null
  } finally { if (isCurrent()) sfcPreviewBusy.value = false }
}
async function retrySfcScopeOptions() { await reloadSfcScope?.() }
async function retrySfcPredictionStatus() {
  if (sfcStatusRecovery) await followPrediction(sfcStatusRecovery.id, undefined, sfcStatusRecovery.binding)
}
const tlcCapability = computed<ContractTlcConditionOptions | null>(() => store.tlcOptions)
const tlcOptionsReady = computed(() => Boolean(tlcCapability.value))
const tlcSolventOptions = computed<TlcSolventOption[]>(() => tlcCapability.value?.solvent_options.filter((item): item is TlcSolventOption => isTlcSolventId(item.id)) ?? [])
const tlcCandidateSystems = computed<TlcCandidateSystem[]>(() => tlcCapability.value?.candidate_systems ?? [])
const tlcReverseRange = computed<TlcReverseRange>(() => mapTlcReverseRange(tlcCapability.value?.reverse))

function persistWorkspace() {
  if (!activeWorkspace) return
  const saved = readPredictionWorkspace(activeWorkspace.key) ?? emptyPredictionWorkspace(activeWorkspace.actor, activeWorkspace.project, activeWorkspace.type, activeWorkspace.tool)
  const task = store.prediction
  const taskMatches = task?.project_id === activeWorkspace.project && task.chromatography_type === activeWorkspace.type && task.tool === activeWorkspace.tool
  const accepted = taskMatches && store.accepted?.id === task!.id ? store.accepted : saved.accepted
  const id = taskMatches ? task!.id : saved.predictionId
  const model = activeWorkspace.type === 'sfc' ? sfcSelectedModelBinding.value?.raw : selectedModel.value
  const value: PredictionWorkspace = {
    ...saved,
    smiles: [...restoredSmiles.value],
    form: activeWorkspace.type === 'tlc' ? (tlcWorkspaceDraft.value ?? saved.form)
      : activeWorkspace.type === 'sfc' ? sfcWorkspaceDraft.value ?? saved.form
        : formDraft.value ?? saved.form,
    modelKey: selectedModelKey.value || (activeWorkspace.type === 'sfc' && model ? JSON.stringify([model.id, model.version]) : saved.modelKey),
    submissionLocked: submissionLocked.value,
    predictionId: id ?? null,
    accepted: accepted && id === accepted.id ? accepted : saved.accepted,
  }
  writePredictionWorkspace(activeWorkspace.key, value)
  workspaceValue = value
}

function updateStructureDraft(slot: number, smiles: string) {
  const next = [...restoredSmiles.value]
  while (next.length <= slot) next.push('')
  next[slot] = smiles
  restoredSmiles.value = next
}

function saveAcceptedForWorkspace(key: string | undefined, accepted: PredictionWorkspace['accepted']) {
  if (!key || !accepted) return
  const saved = readPredictionWorkspace(key)
  if (!saved) return
  const value = { ...saved, predictionId: accepted.id, accepted, submissionLocked: false }
  writePredictionWorkspace(key, value)
  if (activeWorkspace?.key === key) { workspaceValue = value; submissionLocked.value = false }
}

function setWorkspaceSubmissionLocked(key: string | undefined, locked: boolean) {
  if (!key) return
  const workspace = activeWorkspace?.key === key ? activeWorkspace : null
  const saved = readPredictionWorkspace(key) ?? (workspace ? emptyPredictionWorkspace(workspace.actor, workspace.project, workspace.type, workspace.tool) : null)
  if (!saved) return
  writePredictionWorkspace(key, { ...saved, submissionLocked: locked })
  if (activeWorkspace?.key === key) submissionLocked.value = locked
}

function activateWorkspace(actor: string, project: string, type: string, tool: string) {
  if (!actor || !project || !type || !tool) return
  const key = predictionWorkspaceKey(actor, project, type, tool)
  if (activeWorkspace?.key === key) return
  const previousWorkspace = activeWorkspace
  const previousModelKey = selectedModelKey.value
  persistWorkspace()
  workspaceGeneration += 1
  activeWorkspace = { key, actor, project, type, tool }
  workspaceValue = readPredictionWorkspace(key) ?? emptyPredictionWorkspace(actor, project, type, tool)
  store.selectedTool = tool
  if (type === 'tlc') activeMode.value = tool === 'batch' ? 'batch' : 'single'
  else if (type !== 'sfc') activeMode.value = tool === 'batch' ? 'batch' : 'single'
  else sfcSelectedToolId.value = tool
  restoredSmiles.value = [...workspaceValue.smiles]
  formDraft.value = workspaceValue.form
  tlcWorkspaceDraft.value = type === 'tlc' ? workspaceValue.form as TlcToolPanelWorkspaceDraft | null : null
  sfcWorkspaceDraft.value = type === 'sfc' ? workspaceValue.form as SfcSearchWorkspaceDraft | null : null
  if (type === 'sfc' && workspaceValue.modelKey) {
    selectedModelKey.value = workspaceValue.modelKey
  } else selectedModelKey.value = workspaceValue.modelKey || (previousWorkspace?.actor === actor && previousWorkspace.project === project && previousWorkspace.type === type
    && healthyModels.value.some((model) => modelOptionKey(model) === previousModelKey) ? previousModelKey : '')
  selectionContext.value = { type, projectId: project, modelKey: selectedModelKey.value }
  structureDraftRestoreGeneration += 1
  structures.value = []; hplcRawStructures.value = []
  store.prediction = null; store.accepted = null
  if (type === 'sfc') invalidateSfcCandidates()
  submissionLocked.value = workspaceValue.submissionLocked
}

watch([actorId, projectId, () => store.selectedType, workspaceToolId], ([actor, project, type, tool]) => {
  if (projectState.value === 'resolved') activateWorkspace(actor, project, type, tool)
}, { flush: 'sync', immediate: true })
watch([() => store.prediction, () => store.accepted, selectedModelKey, submissionLocked, formDraft, tlcWorkspaceDraft, sfcWorkspaceDraft, restoredSmiles], persistWorkspace, { deep: true })

watch([availableProjects, () => app.accessContext], ([projects, context]) => {
  const ids = projects.map((project) => project.id)
  const requestedProject = typeof route.query.project_id === 'string' && ids.includes(route.query.project_id) ? route.query.project_id : ''
  selectedProjectId.value = ids.includes(selectedProjectId.value) ? selectedProjectId.value : requestedProject || (resolveProjectId(context, null) ?? '')
}, { immediate: true })

function isCurrentProject(generation: number, expectedProjectId: string): boolean {
  return projectGeneration === generation && Boolean(expectedProjectId) && projectId.value === expectedProjectId && projectState.value === 'resolved'
}

function invalidateProjectState() {
  projectGeneration += 1
  submitAttemptGeneration += 1
  loadRequestGeneration += 1
  structureInputGeneration.value += 1
  stopPolling()
  structures.value = []
  hplcRawStructures.value = []
  sfcSelectionGeneration += 1
  sfcOptionsRequestGeneration += 1
  sfcSearchOptions.value = null; sfcOptionsState.value = 'idle'; sfcOptionsError.value = null
  sfcSearchTools.value = []; sfcSearchModels.value = []
  sfcSelectedToolId.value = route.query.tool === 'single_column' || route.query.tool === 'full_column' ? route.query.tool : ''
  selectedModelKey.value = ''
  selectionContext.value = { type: '', projectId: '', modelKey: '' }
  selectionInvalidated.value = false
  store.prediction = null
  store.accepted = null
  replayDraft.value = null
  replayRequested.value = false
  store.sfcOptions = null
  store.sfcCandidateScope = null
  formError.value = null
  submitting.value = false
}

function clearSfcBinding(clearResult = true, preserveModelSelection = false) {
  sfcSelectionGeneration += 1
  sfcOptionsRequestGeneration += 1
  sfcSearchOptions.value = null
  sfcOptionsState.value = 'idle'
  sfcOptionsError.value = null
  store.sfcOptions = null
  store.sfcCandidateScope = null
  if (!preserveModelSelection) selectedModelKey.value = ''
  if (clearResult) { store.prediction = null; store.accepted = null; stopPolling() }
}

async function selectSfcTool(toolId: string) {
  const capabilityTool = sfcCapabilityTools.value.find((item) => item.id === toolId)
  if (!capabilityTool || capabilityTool.health.status !== 'READY' || capabilityTool.schema_version !== '1.1') return
  formError.value = null
  clearSfcBinding()
  sfcSelectedToolId.value = toolId
  if (route.query.tool !== toolId) void router.replace({ query: { ...route.query, tool: toolId } })
  store.selectedTool = toolId
  store.toolSchema = null
  const requestGeneration = ++sfcOptionsRequestGeneration
  const selectionGeneration = sfcSelectionGeneration
  const loadGeneration = loadRequestGeneration
  const projectGenerationAtRequest = projectGeneration
  const expectedProjectId = projectId.value
  const isCurrent = () => requestGeneration === sfcOptionsRequestGeneration && selectionGeneration === sfcSelectionGeneration
    && loadRequestGeneration === loadGeneration && projectGeneration === projectGenerationAtRequest
    && projectId.value === expectedProjectId && store.selectedType === 'sfc' && sfcSelectedToolId.value === toolId
  try {
    const schema = await store.loadTool('sfc', toolId, capabilityTool.schema_version, isCurrent)
    if (!isCurrent()) return
    if (schema.id !== toolId || schema.schema_version !== '1.1' || schema.health.status !== 'READY'
      || !sfcSearchLimitsFromSchema(schema.input_schema, schema.limits)) {
      formError.value = schema.health.reason ?? '此 SFC 工具没有完整 READY 的 1.1 输入 schema；提交已禁用。'
    } else {
      autoSelectUniqueSfcModel(toolId)
    }
  } catch (reason) {
    if (isCurrent()) formError.value = reason instanceof Error ? reason.message : 'SFC 工具 schema 读取失败；提交已禁用。'
  }
}

async function selectSfcModel(selection: { modelId: string | null; modelVersion: string | null }) {
  if (!selection.modelId || !selection.modelVersion) {
    if (selectedModelKey.value || sfcSearchOptions.value || sfcOptionsState.value !== 'idle') clearSfcBinding()
    return
  }
  clearSfcBinding(true, true)
  if (!sfcCapabilityReady.value || !projectId.value) { selectedModelKey.value = ''; return }
  const binding = sfcMappedModels.value.find((item) => item.raw.id === selection.modelId && item.raw.version === selection.modelVersion)
  const tool = sfcSelectedToolChoice.value
  const limits = sfcSearchLimits.value
  if (!binding || binding.choice.health !== 'READY' || !tool || !limits) {
    selectedModelKey.value = ''
    sfcOptionsState.value = 'error'; sfcOptionsError.value = '所选模型、工具或完整 schema 未通过 READY 身份校验。'
    return
  }
  try { createSfc11ExactSelection(binding.raw) }
  catch (reason) { selectedModelKey.value = ''; sfcOptionsState.value = 'error'; sfcOptionsError.value = reason instanceof Error ? reason.message : 'SFC 模型精确身份无效。'; return }
  selectionInvalidated.value = false
  formError.value = null
  selectedModelKey.value = modelOptionKey(binding.raw)
  const selectionGeneration = sfcSelectionGeneration
  const requestGeneration = ++sfcOptionsRequestGeneration
  const loadGeneration = loadRequestGeneration
  const projectGenerationAtRequest = projectGeneration
  const expectedProjectId = projectId.value
  const expectedToolId = sfcSelectedToolId.value
  const isCurrent = () => requestGeneration === sfcOptionsRequestGeneration && selectionGeneration === sfcSelectionGeneration
    && loadRequestGeneration === loadGeneration && projectGeneration === projectGenerationAtRequest
    && projectId.value === expectedProjectId && store.selectedType === 'sfc' && sfcSelectedToolId.value === expectedToolId
    && selectedModelKey.value === modelOptionKey(binding.raw)
  sfcOptionsState.value = 'loading'
  try {
    const result = await store.loadSfc11Options({ project_id: expectedProjectId, model_id: binding.raw.id, model_version: binding.raw.version })
    if (!isCurrent()) return
    const options = optionsForSfcSearch(result.data, binding.raw, tool, expectedProjectId, limits)
    if (!options) throw new Error('服务端 SFC 选项与当前精确模型、工具、项目或 domain 不匹配。')
    sfcSearchOptions.value = options
    sfcOptionsState.value = 'ready'
  } catch (reason) {
    if (isCurrent()) { sfcSearchOptions.value = null; sfcOptionsState.value = 'error'; sfcOptionsError.value = reason instanceof Error ? reason.message : '读取 SFC 候选选项失败。' }
  }
}

function autoSelectUniqueSfcModel(toolId: string) {
  if (!sfcCapabilityReady.value || !projectId.value) return
  const tool = sfcSearchTools.value.find((item) => item.id === toolId)
  if (!tool || tool.health !== 'READY' || !tool.newRequestReady) return
  const models = sfcMappedModels.value.filter((item) => item.choice.health === 'READY' && item.choice.newRequestReady
    && item.choice.model_scope === (tool.requiresColumn ? 'single_column' : 'all_column')
    && (() => { try { createSfc11ExactSelection(item.raw); return true } catch { return false } })())
  if (models.length === 1) void selectSfcModel({ modelId: models[0]!.raw.id, modelVersion: models[0]!.raw.version })
}

function replayInputMatchesForm(type: string, input: ReplayInput, schemaVersion?: string): boolean {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return false
  const value = input as Record<string, unknown>
  const hasKeys = (record: Record<string, unknown>, keys: string[]) => Object.keys(record).length === keys.length && keys.every((key) => key in record)
  const positive = (field: string) => typeof value[field] === 'number' && Number.isFinite(value[field])
  if (type === 'cc') return hasKeys(value, ['column_spec_id', 'eluent_system_id', 'loading_solvent_id', 'sample_mass_mg', 'loading_solvent_volume_ul', 'flow_rate_ml_min']) && ['column_spec_id', 'eluent_system_id', 'loading_solvent_id'].every((key) => typeof value[key] === 'string' && Boolean(value[key])) && ['sample_mass_mg', 'loading_solvent_volume_ul', 'flow_rate_ml_min'].every((key) => positive(key))
  if (type === 'hplc') return schemaVersion === '1.1' && isHplc11Input(input)
  if (type === 'gc') {
    return schemaVersion === '1.1' && isGc11Input(input)
  }
  if (type === 'tlc') return hasKeys(value, ['solvent_components']) && Array.isArray(value.solvent_components) && value.solvent_components.length >= 2 && value.solvent_components.every((entry) => {
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return false
    const component = entry as Record<string, unknown>
    return hasKeys(component, ['solvent_id', 'fraction']) && typeof component.solvent_id === 'string' && typeof component.fraction === 'number' && Number.isFinite(component.fraction)
  })
  return false
}

watch([projectId, projectContextKey], ([next, nextContext], [previous, previousContext]) => {
  if (next !== previous || nextContext !== previousContext) {
    invalidateProjectState()
    if (viewInitialized && next) void loadSelectedType()
  }
}, { flush: 'sync' })

function preferredTool(type: string): string { return type === 'sfc' ? 'full_column' : 'single' }

async function validateHistoricalSfcBinding(prediction: ContractPrediction, isCurrent: () => boolean): Promise<boolean> {
  if (!isCurrent()) return false
  const result = prediction.result as unknown as Record<string, unknown> | undefined
  const provenance = result?.candidate_scope_provenance
  const input = prediction.input_snapshot.input as unknown as Record<string, unknown>
  const scopeId = input.candidate_scope_id
  if (provenance === undefined || provenance === null) {
    if (prediction.schema_version !== '1.1' || typeof scopeId !== 'string' || !scopeId.trim()) return true
    try {
      const { data: options } = await store.loadSfc11Options({ project_id: prediction.project_id, scope_id: scopeId })
      if (isCurrent() && options.project_id === prediction.project_id && options.scope_id === scopeId
        && options.model_id === prediction.model.id && options.model_version === prediction.model.version
        && isNonNegativeSafeCount(options.candidate_count)) {
        sfcBoundScopeCount.value = { scopeId, count: options.candidate_count }
      }
    } catch { /* The optional scope count stays unknown; the persisted result remains readable. */ }
    return true
  }
  const mismatch = () => {
    if (isCurrent()) formError.value = '历史耦合候选结果与其冻结候选范围绑定不一致；已隐藏结果，保留本地恢复指针与只读记录入口。'
    return false
  }
  if (!isSfcScopeProvenance(provenance) || provenance.policy.expansion_mode !== 'coupled_discrete_conditions'
    || typeof scopeId !== 'string' || !scopeId.trim() || provenance.scope_id !== scopeId) return mismatch()

  let options: Awaited<ReturnType<typeof store.loadSfc11Options>>['data']
  try {
    const response = await store.loadSfc11Options({ project_id: prediction.project_id, scope_id: scopeId })
    options = response.data
  } catch {
    if (isCurrent()) formError.value = '读取或核验历史耦合候选范围失败；结果已隐藏，恢复指针与只读记录入口仍保留，可稍后重新读取。'
    return false
  }
  if (!isCurrent()) return false
  if (options.project_id !== prediction.project_id || options.scope_id !== scopeId
    || options.model_id !== prediction.model.id || options.model_version !== prediction.model.version
    || options.candidate_count !== provenance.candidate_count
    || !sameSfcScopeProvenance(options.scope_provenance, provenance)) return mismatch()
  return true
}

async function loadSelectedType() {
  if (!store.selectedType) return
  const loadRequest = ++loadRequestGeneration
  structureInputGeneration.value += 1
  const generation = projectGeneration
  const expectedProjectId = projectId.value
  const selectedType = store.selectedType
  const previousSfcToolId = sfcSelectedToolId.value
  const selectedWorkspaceGeneration = workspaceGeneration
  const selectedWorkspaceKey = activeWorkspace?.key
  const previousSelectionKey = selectionContext.value.type === selectedType && selectionContext.value.projectId === expectedProjectId
    ? selectedModelKey.value || selectionContext.value.modelKey
    : ''
  const isCurrent = () => loadRequestGeneration === loadRequest && projectGeneration === generation && projectId.value === expectedProjectId && store.selectedType === selectedType
    && workspaceGeneration === selectedWorkspaceGeneration && activeWorkspace?.key === selectedWorkspaceKey
  const restoreHistoricalBinding = (prediction: ContractPrediction) => validateHistoricalSfcBinding(prediction, () => isCurrent() && canReadPredictions.value)
  const restoreSavedSfcPrediction = async () => {
    if (selectedType !== 'sfc' || !canReadPredictions.value || !isCurrent()) return
    await restoreWorkspacePrediction('sfc', expectedProjectId, activeWorkspace?.tool ?? sfcSelectedToolId.value,
      () => isCurrent() && canReadPredictions.value, restoreHistoricalBinding)
  }
  formError.value = null; replayDraft.value = null; structures.value = []; hplcRawStructures.value = []; store.prediction = null; store.accepted = null; submitting.value = false
  selectedModelKey.value = ''
  if (selectedType === 'sfc') {
    sfcSelectionGeneration += 1
    sfcSearchOptions.value = null; sfcOptionsState.value = 'idle'; sfcOptionsError.value = null
    sfcSearchTools.value = []; sfcSearchModels.value = []
    store.sfcOptions = null; store.sfcCandidateScope = null
  }
  try {
    if (selectedType === 'sfc' && (typeof route.query.draft_id === 'string' || readReplayPointer(expectedProjectId))) {
      await restoreReplayDraft(selectedType, expectedProjectId, isCurrent)
      if (replayRequested.value) return
    }
    const capability = await store.loadType(selectedType, isCurrent)
    if (!isCurrent()) return
    if (capability.health.status === 'UNAVAILABLE' || (['sfc', 'gc'].includes(selectedType) && capability.health.status !== 'READY')) {
      selectedModelKey.value = ''
      await restoreReplayDraft(selectedType, expectedProjectId, isCurrent)
      if (!replayRequested.value) await restoreSavedSfcPrediction()
      return
    }
    const sfcToolChoices = selectedType === 'sfc'
      ? capability.tools.map((item) => mapSfcToolChoice(item as unknown as { id: string; display_name?: string; schema_version?: string; health?: { status?: string; reason?: string | null } })).filter((item): item is SfcSearchToolChoice => Boolean(item))
      : []
    if (selectedType === 'sfc') {
      sfcSearchTools.value = sfcToolChoices
      sfcSearchModels.value = (capability.models as unknown as Sfc11ModelOption[]).map(mapSfcModelOption).filter((item): item is SfcSearchModelChoice => Boolean(item))
      const initialTool = sfcToolChoices.find((item) => item.id === previousSfcToolId && item.health === 'READY' && item.newRequestReady)
        ?? sfcToolChoices.find((item) => item.health === 'READY' && item.newRequestReady)
        ?? sfcToolChoices.find((item) => item.id === previousSfcToolId && item.health === 'READY')
        ?? sfcToolChoices.find((item) => item.health === 'READY')
        ?? sfcToolChoices[0]
      sfcSelectedToolId.value = initialTool?.id ?? ''
    }
    const requestedTlcTool = selectedType === 'tlc' && typeof route.query.tool === 'string'
      && visibleTlcTools.value.includes(route.query.tool as TlcTool) && route.query.tool !== 'reverse_solvent'
      ? route.query.tool as TlcTool : null
    const requestedSchemaToolId = requestedTlcTool === 'batch' ? 'single' : requestedTlcTool
    const resolvedTlcTool = requestedTlcTool && (requestedTlcTool === 'batch' || capability.tools.some((item) => item.id === requestedSchemaToolId))
      ? requestedTlcTool : null
    const preferredTlcSchemaToolId = resolvedTlcTool === 'batch' ? 'single' : resolvedTlcTool ?? preferredTool(selectedType)
    const tool = selectedType === 'sfc'
      ? capability.tools.find((item) => item.id === sfcSelectedToolId.value)
      : capability.tools.find((item) => item.id === (selectedType === 'tlc' ? preferredTlcSchemaToolId : preferredTool(selectedType)))
        ?? capability.tools.find((item) => item.id === preferredTool(selectedType)) ?? capability.tools[0]
    if (!tool) {
      await restoreReplayDraft(selectedType, expectedProjectId, isCurrent)
      if (selectedType === 'sfc') { if (!replayRequested.value) await restoreSavedSfcPrediction(); return }
      if (selectedType === 'gc') return
      throw new Error('服务端未返回该色谱类型的可用工具')
    }
    if (selectedType === 'sfc' && (tool.health.status !== 'READY' || tool.schema_version !== '1.1')) {
      await restoreReplayDraft(selectedType, expectedProjectId, isCurrent)
      if (!replayRequested.value) formError.value = tool.health.reason ?? '此 SFC 工具尚无 READY 的 1.1 schema；请查看真实服务端能力状态。'
      if (!replayRequested.value) await restoreSavedSfcPrediction()
      return
    }
    if (selectedType === 'gc' && tool.health.status !== 'READY') { await restoreReplayDraft(selectedType, expectedProjectId, isCurrent); return }
    store.selectedTool = tool.id
    await store.loadTool(selectedType, tool.id, tool.schema_version, isCurrent)
    if (!isCurrent()) return
    if (selectedType === 'tlc' && resolvedTlcTool === 'batch') {
      store.selectedTool = 'batch'
      activeMode.value = 'batch'
    } else if (selectedType === 'tlc' && resolvedTlcTool) {
      store.selectedTool = resolvedTlcTool
      activeMode.value = 'single'
    }
    if (selectedType === 'gc' && (tool.schema_version !== '1.1' || store.toolSchema?.schema_version !== '1.1' || store.toolSchema.health.status !== 'READY')) {
      await restoreReplayDraft(selectedType, expectedProjectId, isCurrent)
      return
    }
    if (selectedType === 'sfc') { await restoreReplayDraft(selectedType, expectedProjectId, isCurrent); if (replayRequested.value) return }
    if (selectedType === 'tlc') { await store.loadTlcOptions(isCurrent); if (!isCurrent()) return }
    if (selectedType !== 'sfc') {
      await restoreReplayDraft(selectedType, expectedProjectId, isCurrent)
      if (replayRequested.value) return
    }
    if (selectedType === 'sfc') {
      const loadedToolSchema = store.toolSchema
      if (!loadedToolSchema || loadedToolSchema.id !== tool.id || loadedToolSchema.schema_version !== '1.1' || loadedToolSchema.health.status !== 'READY') throw new Error('SFC 工具 schema 未能与所选 1.1 能力绑定；提交已禁用。')
      const previousBinding = previousSelectionKey
        ? sfcMappedModels.value.find((item) => modelOptionKey(item.raw) === previousSelectionKey
          && item.choice.health === 'READY' && item.choice.newRequestReady
          && item.choice.model_scope === (sfcSelectedToolChoice.value?.requiresColumn ? 'single_column' : 'all_column'))
        : undefined
      if (previousBinding) {
        try {
          createSfc11ExactSelection(previousBinding.raw)
          selectedModelKey.value = previousSelectionKey
          void selectSfcModel({ modelId: previousBinding.raw.id, modelVersion: previousBinding.raw.version })
          selectionInvalidated.value = false
        } catch {
          selectedModelKey.value = ''
          selectionInvalidated.value = true
          formError.value = '已选 SFC 精确模型的 1.1 身份已失效，请重新选择模型。'
        }
      } else {
        selectedModelKey.value = ''
        selectionInvalidated.value = Boolean(previousSelectionKey)
        if (previousSelectionKey) formError.value = '已选 SFC 精确模型在当前工具或服务端 READY 能力中已失效，请重新选择。'
        else autoSelectUniqueSfcModel(tool.id)
      }
      selectionContext.value = { type: selectedType, projectId: expectedProjectId, modelKey: selectedModelKey.value }
    } else {
      if (previousSelectionKey) {
        const stillAvailable = healthyModels.value.some((model) => modelOptionKey(model) === previousSelectionKey)
        selectedModelKey.value = stillAvailable ? previousSelectionKey : ''
        if (stillAvailable) selectionInvalidated.value = false
        if (!stillAvailable) {
          selectionInvalidated.value = true
          formError.value = '模型预处理版本已变化或已不可用；请显式重新选择模型。'
        }
      } else if (selectionContext.value.type === selectedType && selectionContext.value.projectId === expectedProjectId && selectionInvalidated.value) {
        selectedModelKey.value = ''
        formError.value = '模型预处理版本已变化；当前选择仍失效，请显式重新选择模型。'
      } else {
        selectedModelKey.value = healthyModels.value[0] ? modelOptionKey(healthyModels.value[0]) : ''
      }
    }
    selectionContext.value = { type: selectedType, projectId: expectedProjectId, modelKey: selectedModelKey.value }
    const restoreCurrent = () => isCurrent() && canReadPredictions.value
    // Historical SFC records are restored against their own project/type pointer; the current catalog is not used to reinterpret them.
    const restoreBinding = selectedType === 'sfc' ? restoreHistoricalBinding : undefined
    await restoreWorkspacePrediction(selectedType, expectedProjectId, activeWorkspace?.tool ?? store.selectedTool, restoreCurrent, restoreBinding)
  } catch (reason) {
    if (isCurrent()) {
      formError.value = selectedType === 'sfc' ? 'SFC 服务端能力或选项暂不可用；旧选项已清除，当前不会提交。' : reason instanceof Error ? reason.message : '预测能力暂不可用'
      if (selectedType === 'sfc' && !replayRequested.value) await restoreSavedSfcPrediction()
    }
  }
}

async function restoreReplayDraft(type: string, expectedProjectId: string, isCurrent: () => boolean) {
  const routeDraftId = typeof route.query.draft_id === 'string' ? route.query.draft_id : null
  const stored = readReplayPointer(expectedProjectId)
  const pointer = routeDraftId ? { project_id: expectedProjectId, draft_id: routeDraftId } : stored
  if (!pointer || !isCurrent()) return
  replayRequested.value = true
  selectedModelKey.value = ''
  if (!canReadPredictions.value) {
    replayDraft.value = null
    clearReplayPointer(pointer)
    formError.value = '缺少预测读取权限；已清除该项目的草稿指针，不会请求或提交。'
    return
  }
  replayLoading.value = true
  try {
    const draft = await getReplayDraft(pointer)
    if (!isCurrent()) return
    if (draft.project_id !== expectedProjectId) throw new Error('草稿与当前项目或色谱类型不匹配')
    if (draft.chromatography_type !== type) {
      if (!routeDraftId) {
        // The project-scoped pointer may refer to another type's valid draft.
        // Leave that draft and pointer intact, and continue loading this page normally.
        replayRequested.value = false
        replayDraft.value = null
        return
      }
      throw new Error('草稿与当前项目或色谱类型不匹配')
    }
    replayDraft.value = draft
    if (type === 'hplc') {
      if (draft.schema_version === '1.0' && isHplc11HistoricalInput(draft.input)) {
        selectedModelKey.value = ''
        formError.value = 'HPLC 1.0 历史草稿没有提交时的原始 SMILES，只能查看，不能重新执行；请创建新的 1.1 预测。'
        return
      }
      if (draft.schema_version !== '1.1' || !isHplc11Input(draft.input)) throw new Error('HPLC 草稿不是可验证的 1.1 原始输入；已保留草稿查看，不能执行。')
      if (typeof draft.model_selection.preprocessing_profile_id !== 'string' || typeof draft.model_selection.preprocessing_profile_sha256 !== 'string') {
        selectedModelKey.value = ''
        formError.value = '此 HPLC 草稿没有完整的精确预处理版本，只能查看；请创建新的 1.1 预测。'
        return
      }
    }
    if (type === 'gc' && (draft.schema_version !== '1.1' || !isGc11Input(draft.input) || !isGc11DraftSelection(draft.model_selection))) {
      selectedModelKey.value = ''
      formError.value = 'GC 1.0 历史草稿或缺少完整 profile 的草稿只读保留；请使用当前 READY GC 1.1 能力创建新预测。'
      return
    }
    if (type === 'cc' && (!draft.model_selection.preprocessing_profile_id || !draft.model_selection.preprocessing_profile_sha256)) {
      selectedModelKey.value = ''
      selectionInvalidated.value = true
      selectionContext.value = { type, projectId: expectedProjectId, modelKey: '' }
      formError.value = '此 CC 历史草稿没有预处理版本，只能查看原输入和结果；请重新创建草稿后提交。'
      return
    }
    if (draft.chromatography_type === 'sfc') {
      const modifierRefs = (draft.input as { modifier_components?: { structure_ref: string }[] }).modifier_components
      const snapshots = draft.modifier_structure_snapshots
      const mainValid = draft.structure_refs.length === 2 && draft.structure_snapshots?.length === 2 && draft.structure_snapshots.every((item, index) => item.project_id === expectedProjectId && item.id === draft.structure_refs[index]?.id && Boolean(item.canonical_smiles && item.stereochemistry_status))
      const modifierValid = Array.isArray(modifierRefs) && Boolean(snapshots) && snapshots!.length === modifierRefs.length && snapshots!.every((item, index) => item.project_id === expectedProjectId && item.id === modifierRefs[index]?.structure_ref && Boolean(item.canonical_smiles && item.stereochemistry_status))
      if (!mainValid || !modifierValid) throw new Error('SFC 主结构或 Modifier 快照与草稿引用/项目不匹配；当前不可编辑或提交。')
      selectedModelKey.value = ''
      formError.value = draft.schema_version === '1.0'
        ? 'SFC 1.0 历史草稿只读保留；不会补候选范围或替换模型。请创建新的 SFC 1.1 搜索。'
        : 'SFC 草稿冻结输入只读保留；不会自动创建候选范围或重新绑定模型。请创建新的 SFC 1.1 搜索。'
      return
    }
    if (draft.tool !== 'single') throw new Error('此工具暂不能安全加载为可编辑表单；草稿可查看，提交已停用。')
    const capabilityTool = store.typeCapability?.tools.find((item) => item.id === draft.tool)
    if (!store.toolSchema || store.toolSchema.id !== draft.tool || !capabilityTool || capabilityTool.schema_version !== draft.schema_version || store.toolSchema.schema_version !== capabilityTool.schema_version || draft.schema_version !== store.toolSchema.schema_version) {
      throw new Error('草稿工具或 schema 版本与当前服务端能力不匹配；不会按其他工具或表单提交。')
    }
    const expectedStructures = ['cc', 'hplc', 'sfc'].includes(type) ? 2 : 1
    if (draft.structure_refs.length !== expectedStructures || !draft.structure_snapshots || draft.structure_snapshots.length !== expectedStructures || draft.structure_snapshots.some((item, index) => item.project_id !== expectedProjectId || item.id !== draft.structure_refs[index]?.id || !item.canonical_smiles || !item.stereochemistry_status)) throw new Error('草稿缺少按原顺序绑定项目的完整结构快照；不会回填或提交。')
    if (!replayInputMatchesForm(type, draft.input, draft.schema_version)) throw new Error('草稿输入字段不完整或不符合当前类型表单；不会替换默认值后提交。')
    if (type === 'cc') {
      const inputError = ccFormValueError(draft.input as unknown as CcFormValue, ccFormCapability.value)
      if (inputError) throw new Error(`CC 重放草稿与当前服务端能力不匹配：${inputError}；已保留原始草稿值，当前不会自动修正。`)
    }
    if (type === 'tlc' && (draft.input as TlcInput).solvent_components.some((item) => !tlcSolventOptions.value.some((option) => option.id === item.solvent_id))) throw new Error('草稿 TLC 溶剂不在当前服务端选项内；不会替换为其他溶剂。')
    const baseModel = healthyModels.value.find((model) => model.id === draft.model_selection.model_id && model.version === draft.model_selection.model_version)
    if (!baseModel) throw new Error(`原模型 ${draft.model_selection.model_id} ${draft.model_selection.model_version} 当前不可用；不会自动切换 Champion。`)
    const exactModel = healthyModels.value.find((model) => exactModelIdentityMatches(draft.model_selection, model as PredictionCandidateModelOption))
    if (!exactModel) {
      selectedModelKey.value = ''
      selectionInvalidated.value = true
      selectionContext.value = { type, projectId: expectedProjectId, modelKey: '' }
      formError.value = '草稿绑定的模型预处理版本与当前能力不一致；原草稿保持只读，请重新创建草稿。'
      return
    }
    try { createExactModelSelection(exactModel as PredictionCandidateModelOption, type) }
    catch (reason) {
      if (['cc', 'hplc', 'gc'].includes(type) && reason instanceof Error) {
        selectedModelKey.value = ''
        selectionInvalidated.value = true
        formError.value = reason.message
        return
      }
      throw reason
    }
    selectedModelKey.value = modelOptionKey(exactModel)
    selectionContext.value = { type, projectId: expectedProjectId, modelKey: selectedModelKey.value }
    if (type === 'gc') {
      const inputError = gcFormValueError(draft.input, gcFormCapability.value)
      if (inputError) throw new Error(`GC 重放草稿与当前模型能力不匹配：${inputError}；已保留原值，不会钳制或提交。`)
    }
    if (routeDraftId && !stored) saveReplayPointer({ project_id: expectedProjectId, draft_id: draft.id })
  } catch (reason) {
    if (!isCurrent()) return
    if ((reason instanceof ApiClientError && [403, 404].includes(reason.status)) || reason instanceof ReplayIdentityError) {
      replayDraft.value = null
      clearReplayPointer(pointer)
      formError.value = '重放草稿无权访问或已失效；已清除该项目的本地草稿指针。'
    } else formError.value = reason instanceof Error ? reason.message : '读取重放草稿失败'
  } finally { if (isCurrent()) replayLoading.value = false }
}

async function startNewPrediction() {
  if (submitting.value || replayLoading.value || submissionLocked.value) return
  const draftId = replayDraft.value?.id ?? (typeof route.query.draft_id === 'string' ? route.query.draft_id : null)
  if (draftId) clearReplayPointer({ project_id: projectId.value, draft_id: draftId })
  stopPolling()
  submitAttemptGeneration += 1
  loadRequestGeneration += 1
  replayDraft.value = null; replayRequested.value = false; formError.value = null
  selectedModelKey.value = ''; selectionInvalidated.value = false
  selectionContext.value = { type: store.selectedType, projectId: projectId.value, modelKey: '' }
  restoredSmiles.value = []; structures.value = []; hplcRawStructures.value = []
  formDraft.value = null; tlcWorkspaceDraft.value = null; sfcWorkspaceDraft.value = null
  store.prediction = null; store.accepted = null
  if (store.selectedType === 'sfc') invalidateSfcCandidates()
  workspaceGeneration += 1
  if (activeWorkspace) {
    workspaceValue = emptyPredictionWorkspace(activeWorkspace.actor, activeWorkspace.project, activeWorkspace.type, activeWorkspace.tool)
    writePredictionWorkspace(activeWorkspace.key, workspaceValue)
  }
  if (typeof route.query.draft_id === 'string') {
    const query = { ...route.query }
    delete query.draft_id
    await router.replace({ query })
  } else await loadSelectedType()
}

watch(() => store.selectedType, () => { submitAttemptGeneration += 1; if (viewInitialized) void loadSelectedType() })
watch(routeType, (type) => { if (type && type !== store.selectedType) store.selectedType = type })
watch(() => route.query.draft_id, () => {
  if (!viewInitialized) return
  replayDraft.value = null; replayRequested.value = false; formError.value = null
  void loadSelectedType()
})

function onNormalized(structure: Structure, slot: number) {
  if (structure.project_id !== projectId.value || projectState.value !== 'resolved') return
  const next = [...structures.value]; next[slot] = structure; structures.value = next
  const rawNext = [...hplcRawStructures.value]; rawNext[slot] = null; hplcRawStructures.value = rawNext
}
function submissionWasRejected(reason: unknown): boolean {
  return reason instanceof ApiClientError && reason.status >= 400 && reason.status < 500 && reason.status !== 408
}
async function normalizeStructureTargets(
  targetIds: readonly string[],
  existing: ReadonlyMap<string, Structure>,
  expectedType: string,
  expectedProjectId: string,
  selectionIsCurrent: () => boolean = () => true,
): Promise<{ structures: Structure[]; rawSmiles: string[] } | null> {
  const generation = projectGeneration
  const loadGeneration = loadRequestGeneration
  const inputGeneration = structureInputGeneration.value
  const targets = targetIds.map((id) => structureWorkspace.targets.get(id) ?? null)
  const rawSmiles = targets.map((target) => target?.getSmiles() ?? '')
  if (targets.some((target, index) => !target || target.projectId !== expectedProjectId || !rawSmiles[index]?.trim())) {
    formError.value = '请填写全部结构后再提交；输入内容已保留。'
    return null
  }
  const isCurrentContext = () => isCurrentProject(generation, expectedProjectId)
    && loadRequestGeneration === loadGeneration && structureInputGeneration.value === inputGeneration
    && store.selectedType === expectedType && selectionIsCurrent()
  const isCurrent = () => isCurrentContext()
    && targets.every((target, index) => Boolean(target && target.projectId === expectedProjectId && target.getSmiles() === rawSmiles[index]))
  const normalized = await Promise.all(targets.map(async (target, index) => {
    const previous = existing.get(targetIds[index]!)
    if (previous?.project_id === expectedProjectId && previous.id) return previous
    return target!.normalize()
  }))
  if (!isCurrentContext()) return null
  if (!isCurrent()) {
    formError.value = '结构输入在校验过程中发生变化；本次提交已取消，请检查输入后重新提交。'
    return null
  }
  if (normalized.some((item) => !item || item.project_id !== expectedProjectId || !item.id)) {
    formError.value = '结构校验未通过；请检查分子结构，输入内容已保留，未创建预测任务。'
    return null
  }
  return { structures: normalized as Structure[], rawSmiles }
}
function onHplcNormalizedWithRaw(payload: { rawSmiles: string; structure: Structure }, slot: number) {
  if (store.selectedType !== 'hplc' || !payload || typeof payload.rawSmiles !== 'string' || !payload.rawSmiles.length
    || payload.structure.project_id !== projectId.value || projectState.value !== 'resolved'
    || structures.value[slot]?.id !== payload.structure.id || structures.value[slot]?.project_id !== projectId.value) return
  const next = [...hplcRawStructures.value]
  next[slot] = { rawSmiles: payload.rawSmiles, structureId: payload.structure.id, projectId: payload.structure.project_id, generation: structureInputGeneration.value }
  hplcRawStructures.value = next
}
function invalidateStructureSlot(slot: number) {
  const next = [...structures.value]; next[slot] = null; structures.value = next
  const rawNext = [...hplcRawStructures.value]; rawNext[slot] = null; hplcRawStructures.value = rawNext
}
function stopPolling() { if (pollTimer.value !== null) window.clearTimeout(pollTimer.value); pollTimer.value = null }
async function restoreWorkspacePrediction(
  type: string,
  expectedProjectId: string,
  expectedTool: string,
  isCurrent: () => boolean,
  validateExtra?: (prediction: ContractPrediction) => boolean | Promise<boolean>,
) {
  const workspace = activeWorkspace
  const value = workspaceValue
  if (!workspace || !value || workspace.type !== type || workspace.project !== expectedProjectId
    || workspace.tool !== expectedTool || !value.predictionId || !canReadPredictions.value || !isCurrent()) return null
  const id = value.predictionId
  const identity = workspace.key
  const generation = workspaceGeneration
  const current = () => generation === workspaceGeneration && activeWorkspace?.key === identity && isCurrent()
  if (value.accepted?.id === id) store.accepted = value.accepted
  try {
    const restored = await store.getPrediction(id, current, expectedProjectId, type, async (prediction) =>
      prediction.id === id && prediction.project_id === expectedProjectId
      && prediction.chromatography_type === type && prediction.tool === expectedTool
      && (!validateExtra || await validateExtra(prediction)))
    if (!current() || restored.id !== id || restored.project_id !== expectedProjectId
      || restored.chromatography_type !== type || restored.tool !== expectedTool) return null
    if (!isPredictionTerminalStatus(restored.status)) void followPrediction(id, undefined, undefined, expectedTool)
    return restored
  } catch (reason) {
    if (current()) formError.value = type === 'sfc' && reason instanceof Error && reason.message === 'Prediction binding mismatch'
      ? '历史 SFC 结果与其候选范围绑定不一致；已隐藏结果，保留本地恢复指针与只读记录入口。'
      : reason instanceof Error ? reason.message : '恢复预测状态失败。'
    return null
  }
}

async function followPrediction(id: string, expectedGcSelection?: Gc11ExactProfileSelection, expectedSfcBinding?: SfcFollowBinding, expectedTool = activeWorkspace?.tool ?? store.selectedTool) {
  const generation = projectGeneration
  const requestGeneration = loadRequestGeneration
  const currentWorkspaceGeneration = workspaceGeneration
  const currentWorkspaceKey = activeWorkspace?.key
  const expectedProjectId = projectId.value
  const expectedType = store.selectedType
  stopPolling()
  const isCurrent = () => isCurrentProject(generation, expectedProjectId) && loadRequestGeneration === requestGeneration
    && workspaceGeneration === currentWorkspaceGeneration && activeWorkspace?.key === currentWorkspaceKey
    && store.selectedType === expectedType && workspaceToolId.value === expectedTool && canReadPredictions.value
    && (!expectedSfcBinding || (sfcSelectionGeneration === expectedSfcBinding.selectionGeneration && sfcSelectedToolId.value === expectedSfcBinding.toolId && selectedModelKey.value === modelOptionKey(expectedSfcBinding.model)))
  const validateBinding = expectedType === 'sfc' && expectedSfcBinding
      ? (loaded: ContractPrediction) => validateSfcPredictionBinding(loaded, expectedProjectId, expectedSfcBinding.model, expectedSfcBinding.toolId,
        isCurrent, expectedSfcBinding.scopeId, expectedSfcBinding.expansionMode, expectedSfcBinding.provenance, expectedSfcBinding.candidateCount)
      : expectedType === 'gc' && expectedGcSelection
        ? (loaded: ContractPrediction) => {
          const snapshot = loaded.model as unknown as Record<string, unknown>
          return loaded.chromatography_type === 'gc'
            && loaded.schema_version === '1.1'
            && gcPredictionModelSnapshotError(loaded.model, loaded.schema_version) === null
            && snapshot.id === expectedGcSelection.model_id
            && snapshot.version === expectedGcSelection.model_version
            && snapshot.preprocessing_profile_id === expectedGcSelection.preprocessing_profile_id
            && snapshot.preprocessing_profile_sha256 === expectedGcSelection.preprocessing_profile_sha256
        }
        : undefined
  try {
      if (expectedSfcBinding) { sfcStatusRecovery = { id, binding: expectedSfcBinding }; sfcAcceptedId.value = id; sfcPredictionStatusError.value = null }
      const prediction = await store.getPrediction(id, isCurrent, expectedProjectId, expectedType, async (loaded) =>
        loaded.id === id && loaded.tool === expectedTool && (!validateBinding || await validateBinding(loaded)))
      if (!isCurrent()) return
      if (prediction.id !== id || prediction.project_id !== expectedProjectId || prediction.chromatography_type !== expectedType || prediction.tool !== expectedTool) { store.prediction = null; store.accepted = null; return }
      if (!isPredictionTerminalStatus(prediction.status)) pollTimer.value = window.setTimeout(() => { void followPrediction(id, expectedGcSelection, expectedSfcBinding, expectedTool) }, 1500)
  } catch (reason) {
    if (!isCurrent()) return
    if (expectedType === 'sfc' && expectedSfcBinding) {
      sfcPredictionStatusError.value = reason instanceof Error && reason.message === 'Prediction binding mismatch'
        ? 'SFC 结果与当前项目、模型、工具或候选范围不匹配；结果已隐藏，可重新核验此任务。'
        : '读取任务状态或核验候选范围失败；任务 ID 已保留，请重新读取。'
    } else formError.value = expectedType === 'gc' && expectedGcSelection ? 'GC 1.1 返回模型或预处理身份与本次精确选择不匹配；结果已隐藏。' : reason instanceof Error ? reason.message : '预测状态读取失败'
  }
}

async function submitInput(input: CcFormValue | HplcFormValue | GcFormValue | TlcInput) {
  if (submitting.value || submissionLocked.value) return
  if (serviceActionsUnavailable.value) { formError.value = '连接不可用；当前不会提交预测，若曾提交任务请按任务 ID 核实状态。'; return }
  if (replayRequested.value && !replayDraft.value) { formError.value = '重放草稿尚未完成校验；当前不会提交。'; return }
  const model = selectedModel.value
  if (replayDraft.value && (!replayUiSupported.value || !model || !exactModelIdentityMatches(replayDraft.value.model_selection, model as PredictionCandidateModelOption))) { formError.value = '重放草稿的精确模型或表单不匹配；当前不会提交。'; return }
  if (!canCreatePredictions.value || !projectId.value || !store.toolSchema || !model || !structureReady.value) { formError.value = '访问权限、项目、结构、模型或工具契约尚未就绪；CC 需要两个不同的已验证结构。'; return }
  if (store.selectedType === 'gc' && (!gcCapabilityReady.value || !isGc11Input(input) || gcFormValueError(input, gcFormCapability.value))) {
    formError.value = gcFormValueError(input, gcFormCapability.value) ?? 'GC 1.1 当前模型或完整 READY 输入 schema 尚未匹配；当前不会提交。'
    return
  }
  let modelSelection: PredictionCandidateExactModelSelection
  try {
    modelSelection = store.selectedType === 'hplc'
      ? createHplc11Selection(model as Hplc11ModelOption)
      : store.selectedType === 'gc'
        ? createGc11Selection(model as Gc11ModelOption)
      : createExactModelSelection(model as PredictionCandidateModelOption, store.selectedType)
  }
  catch (reason) { formError.value = reason instanceof Error ? reason.message : '模型身份无效；当前不会提交。'; return }
  const attempt = ++submitAttemptGeneration
  const expectedType = store.selectedType
  const expectedProjectId = projectId.value
  const expectedModelKey = selectedModelKey.value
  const expectedTool = store.selectedTool
  const submitLoadGeneration = loadRequestGeneration
  const submitWorkspaceGeneration = workspaceGeneration
  const submitWorkspaceKey = activeWorkspace?.key
  const isCurrentAttempt = () => projectId.value === expectedProjectId && store.selectedType === expectedType
    && loadRequestGeneration === submitLoadGeneration && selectedModelKey.value === expectedModelKey
    && store.selectedTool === expectedTool && submitAttemptGeneration === attempt
    && workspaceGeneration === submitWorkspaceGeneration && activeWorkspace?.key === submitWorkspaceKey
  submitting.value = true
  const targetIds = requiredStructureTargetIds.value
  const reusable = new Map<string, Structure>()
  targetIds.forEach((id, index) => { const structure = structures.value[index]; if (structure) reusable.set(id, structure) })
  const normalized = replayDraft.value
    ? { structures: replayStructureIds.value.map((id) => ({ id, project_id: replayDraft.value!.project_id } as Structure)), rawSmiles: [] }
    : await normalizeStructureTargets(targetIds, reusable, expectedType, expectedProjectId, isCurrentAttempt)
  if (attempt !== submitAttemptGeneration) return
  if (!normalized) { submitting.value = false; return }
  if (projectId.value !== expectedProjectId || store.selectedType !== expectedType || loadRequestGeneration !== submitLoadGeneration || selectedModelKey.value !== expectedModelKey) {
    formError.value = '项目、色谱类型或模型在结构校验过程中发生变化；本次预测已取消。'
    submitting.value = false
    return
  }
  const structureRefs = normalized.structures.map((item) => item.id)
  if (structureRefs.some((value) => !value)) { formError.value = '请按顺序完成所有结构槽位的规范化。'; submitting.value = false; return }
  if (store.selectedType === 'cc' && !areDistinctStructureRefs(structureRefs)) { formError.value = 'CC 必须恰好使用两个不同的已规范化结构；当前不会提交。'; submitting.value = false; return }
  if (store.selectedType === 'hplc') {
    const rawPair = replayDraft.value ? hplcReplayRawPair.value : normalized.rawSmiles
    if (!hplcRawStructuresReady.value || !isHplc11RawPair(rawPair) || !validHplc11StructureRefs(structureRefs)) {
      formError.value = 'HPLC 1.1 必须将两条本次原始 SMILES 按顺序绑定到当前项目的两个不同结构；请重新确认输入。'
      submitting.value = false
      return
    }
    const hplcBody: Hplc11CreateBody = {
      chromatography_type: 'hplc',
      tool: 'single',
      schema_version: '1.1',
      project_id: projectId.value,
      model_selection: modelSelection,
      structure_refs: [structureRefs[0]!, structureRefs[1]!],
      input: {
        ...(input as HplcFormValue),
        model_input_smiles: [rawPair[0], rawPair[1]],
      } satisfies Hplc11Input,
    }
    const generation = projectGeneration
    const expectedProjectId = projectId.value
    const submitLoadGeneration = loadRequestGeneration
    const isCurrent = () => isCurrentProject(generation, expectedProjectId)
      && loadRequestGeneration === submitLoadGeneration && submitAttemptGeneration === attempt
      && store.selectedType === 'hplc' && store.selectedTool === expectedTool && submitAttemptGeneration === attempt && isCurrentAttempt()
    formError.value = null
    setWorkspaceSubmissionLocked(submitWorkspaceKey, true)
    try { const accepted = await store.createHplc11(hplcBody, isCurrent); saveAcceptedForWorkspace(submitWorkspaceKey, accepted.data); if (isCurrent()) await followPrediction(accepted.data.id) }
    catch (reason) { const rejected = submissionWasRejected(reason); if (rejected) setWorkspaceSubmissionLocked(submitWorkspaceKey, false); if (isCurrent()) { submissionLocked.value = !rejected; formError.value = rejected ? (reason instanceof Error ? reason.message : '预测提交失败') : '预测提交状态未确认；请先核对预测记录，当前不会重复提交。' } }
    finally { if (isCurrent()) submitting.value = false }
    return
  }
  if (store.selectedType === 'gc') {
    const structureRef = structureRefs[0]
    if (!isGc11StructureRefs([structureRef])) {
      formError.value = 'GC 1.1 必须恰好绑定一个当前项目的结构引用；当前不会提交。'
      submitting.value = false
      return
    }
    const gcModelSelection = createGc11Selection(model as Gc11ModelOption)
    const gcBody: Gc11CreateBody = {
      chromatography_type: 'gc', tool: 'single', schema_version: '1.1', project_id: projectId.value,
      model_selection: gcModelSelection, structure_refs: [structureRef], input: input as GcFormValue,
    }
    const generation = projectGeneration
    const expectedProjectId = projectId.value
    const submitLoadGeneration = loadRequestGeneration
    const isCurrent = () => isCurrentProject(generation, expectedProjectId)
      && loadRequestGeneration === submitLoadGeneration && store.selectedType === 'gc' && store.selectedTool === expectedTool && submitAttemptGeneration === attempt && isCurrentAttempt()
    formError.value = null
    setWorkspaceSubmissionLocked(submitWorkspaceKey, true)
    try { const accepted = await store.createGc11(gcBody, isCurrent); saveAcceptedForWorkspace(submitWorkspaceKey, accepted.data); if (isCurrent()) await followPrediction(accepted.data.id, gcModelSelection) }
    catch (reason) { const rejected = submissionWasRejected(reason); if (rejected) setWorkspaceSubmissionLocked(submitWorkspaceKey, false); if (isCurrent()) { submissionLocked.value = !rejected; formError.value = rejected ? (reason instanceof Error ? reason.message : 'GC 预测提交失败') : 'GC 预测提交状态未确认；请先核对预测记录，当前不会重复提交。' } }
    finally { if (isCurrent()) submitting.value = false }
    return
  }
  const common = { project_id: projectId.value, schema_version: replayDraft.value?.schema_version ?? store.toolSchema.schema_version, model_selection: modelSelection, structure_refs: structureRefs }
  let body: ContractPredictionCreateBody
  if (store.selectedType === 'cc') body = { ...common, chromatography_type: 'cc', tool: 'single', input: input as CcFormValue }
  else if (store.selectedType === 'tlc') body = { ...common, chromatography_type: 'tlc', tool: 'single', input: input as TlcInput }
  else { formError.value = '该工具正在等待冻结契约接入。'; return }
  const generation = projectGeneration
  const isCurrent = () => isCurrentProject(generation, expectedProjectId) && submitAttemptGeneration === attempt && isCurrentAttempt()
  formError.value = null
  setWorkspaceSubmissionLocked(submitWorkspaceKey, true)
  try { const accepted = await store.create(body, isCurrent); saveAcceptedForWorkspace(submitWorkspaceKey, accepted.data); if (isCurrent()) await followPrediction(accepted.data.id) }
  catch (reason) { const rejected = submissionWasRejected(reason); if (rejected) setWorkspaceSubmissionLocked(submitWorkspaceKey, false); if (isCurrent()) { submissionLocked.value = !rejected; formError.value = rejected ? (reason instanceof Error ? reason.message : '预测提交失败') : '预测提交状态未确认；请先核对预测记录，当前不会重复提交。' } }
  finally { if (isCurrent()) submitting.value = false }
}

function blockUnfrozenTool(message: string) { formError.value = message }
async function submitSfc(value: SfcSearchFormValue) {
  if (submitting.value || submissionLocked.value) return
  if (serviceActionsUnavailable.value) { blockUnfrozenTool('连接不可用；当前不会创建候选范围或提交预测。'); return }
  if (sfcSubmissionLocked.value) return
  const binding = sfcSelectedModelBinding.value
  const model = binding?.raw
  const tool = sfcSelectedCapabilityTool.value
  const toolChoice = sfcSelectedToolChoice.value
  const options = sfcSearchOptions.value
  const limits = sfcSearchLimits.value
  const expansionMode = value.expansionMode
  if (replayRequested.value || replayDraft.value) { blockUnfrozenTool('SFC 草稿只读保留；请使用当前服务端 1.1 能力创建新搜索。'); return }
  if (!canCreatePredictions.value || !projectId.value || projectState.value !== 'resolved' || !sfcCapabilityReady.value
    || !model || !tool || !toolChoice || !limits || !options || !sfcFormReady.value || !structureReady.value
    || value.projectId !== projectId.value || value.toolId !== tool.id || value.modelId !== model.id || value.modelVersion !== model.version
    || !options.supportedExpansionModes?.includes(expansionMode)
    || value.model_scope !== (toolChoice.requiresColumn ? 'single_column' : 'all_column')) {
    blockUnfrozenTool('SFC 访问权限、项目、工具、精确模型、选项或两个结构尚未形成完整绑定；当前不会提交。'); return
  }
  if (!Array.isArray(value.candidateAxes.column) || value.candidateAxes.column.length === 0
    || (tool.id === 'single_column' && (!value.column_id || value.candidateAxes.column.length !== 1 || value.candidateAxes.column[0] !== value.column_id || !binding.choice.allowed_columns?.includes(value.column_id)))) {
    blockUnfrozenTool('SFC 列候选与所选单柱模型不匹配；当前不会创建候选范围。'); return
  }
  let modelSelection: PredictionCandidateExactModelSelection
  try { modelSelection = createSfc11ExactSelection(model) as PredictionCandidateExactModelSelection }
  catch (reason) { blockUnfrozenTool(reason instanceof Error ? reason.message : 'SFC 模型身份无效；当前不会提交。'); return }
  const attempt = ++submitAttemptGeneration
  const generation = projectGeneration
  const loadGeneration = loadRequestGeneration
  const selectionGeneration = sfcSelectionGeneration
  const candidateGeneration = sfcCandidateGeneration
  const expectedProjectId = projectId.value
  const expectedToolId = tool.id
  const expectedModelKey = modelOptionKey(model)
  const expectedWorkspaceGeneration = workspaceGeneration
  const expectedWorkspaceKey = activeWorkspace?.key
  const isCurrent = () => isCurrentProject(generation, expectedProjectId) && loadRequestGeneration === loadGeneration
    && store.selectedType === 'sfc' && sfcSelectionGeneration === selectionGeneration && sfcCandidateGeneration === candidateGeneration
    && sfcSelectedToolId.value === expectedToolId && selectedModelKey.value === expectedModelKey && submitAttemptGeneration === attempt
    && workspaceGeneration === expectedWorkspaceGeneration && activeWorkspace?.key === expectedWorkspaceKey
  const modifierTargetIds = [...structureWorkspace.targets.keys()].filter((id) => /^modifier:\d+$/.test(id))
    .sort((left, right) => Number(left.slice('modifier:'.length)) - Number(right.slice('modifier:'.length)))
  if (modifierTargetIds.length !== value.modifiers.length) {
    blockUnfrozenTool('SFC Modifier 输入与当前表单不一致；请检查 Modifier 结构后重试。'); return
  }
  const targetIds = ['primary', 'secondary', ...modifierTargetIds]
  const reusableStructures = new Map<string, Structure>()
  if (structures.value[0]) reusableStructures.set('primary', structures.value[0]!)
  if (structures.value[1]) reusableStructures.set('secondary', structures.value[1]!)
  modifierTargetIds.forEach((id, index) => {
    const ref = value.modifiers[index]?.structureRef
    if (ref) reusableStructures.set(id, { id: ref, project_id: expectedProjectId } as Structure)
  })
  formError.value = null; submitting.value = true
  const normalized = await normalizeStructureTargets(targetIds, reusableStructures, 'sfc', expectedProjectId, isCurrent)
  if (attempt !== submitAttemptGeneration) return
  if (!normalized) { submitting.value = false; return }
  if (!isCurrent()) {
    formError.value = 'SFC 项目、模型、工具或候选范围在结构校验过程中发生变化；本次请求已取消。'
    submitting.value = false
    return
  }
  const primaryRefs = normalized.structures.slice(0, 2).map((item) => item.id)
  const modifierRefs = normalized.structures.slice(2).map((item) => item.id)
  if (!areDistinctStructureRefs(primaryRefs) || !areDistinctStructureRefs(modifierRefs, modifierRefs.length)) {
    formError.value = 'SFC 主体结构或 Modifier 结构重复；请调整分子结构。'
    submitting.value = false
    return
  }
  const axes = value.candidateAxes
  const candidateBudget = Math.min(limits.candidateLimit, tool.limits.max_candidates, tool.limits.max_evaluations)
  if (!Number.isInteger(candidateBudget) || candidateBudget < 1) { blockUnfrozenTool('当前工具没有有效的候选评估预算；不会创建候选范围。'); submitting.value = false; return }
  sfcCandidateError.value = null; sfcScopeOptionsError.value = null; sfcPredictionError.value = null; sfcPredictionStatusError.value = null
  let stage: 'preview' | 'scope' | 'scope-options' | 'prediction' = 'preview'
  try {
    const preview = await previewSfcCandidates(axes, expansionMode)
    if (!isCurrent() || !preview || preview.status !== 'READY') return
    stage = 'scope'
    const scopeBody: Sfc11ScopeCreate = {
      project_id: expectedProjectId, axes, max_candidates: candidateBudget, model_selection: modelSelection, expansion_mode: expansionMode,
    }
    const previewPolicy = isSfcPolicy(preview.policy) ? preview.policy : null
    const scopeKey = JSON.stringify([scopeBody, expansionMode, previewPolicy?.policy_version ?? null,
      previewPolicy?.candidate_source_sha256 ?? null, preview.observed_count ?? null, preview.unobserved_count ?? null])
    const scopeResult = preparedSfcScope?.key === scopeKey ? { data: preparedSfcScope.scope } : await store.createSfc11CandidateScope(scopeBody)
    if (!isCurrent()) return
    const scope = scopeResult.data
    if (!sfcScopeMatchesSelection(scope, model, expectedProjectId, axes) || scope.candidate_count > candidateBudget
      || scope.candidate_count > tool.limits.max_candidates || scope.candidate_count > tool.limits.max_evaluations
      || !scopeMatchesExpansion(scope, preview, expansionMode)) {
      throw new Error('服务端候选范围的项目、模型、条件或候选预算与当前请求不匹配。')
    }
    const expectedProvenance = scopeProvenance(scope, expansionMode)
    if (expansionMode === 'coupled_discrete_conditions' && !expectedProvenance) {
      throw new Error('服务端受约束组合范围缺少有效策略或来源计数。')
    }
    sfcBoundScopeCount.value = { scopeId: scope.id, count: scope.candidate_count }
    preparedSfcScope = { key: scopeKey, scope }
    stage = 'scope-options'
    async function readScopeOptions() {
      const optionsResult = await store.loadSfc11Options({ project_id: expectedProjectId, scope_id: scope.id })
      if (!isCurrent() || preparedSfcScope?.key !== scopeKey) return
      const boundOptions = optionsForSfcSearch(optionsResult.data, model!, toolChoice!, expectedProjectId, limits!, scope.id)
      if (!boundOptions || optionsResult.data.candidate_count !== scope.candidate_count || optionsResult.data.max_candidates !== scope.max_candidates
        || !boundOptions.supportedExpansionModes?.includes(expansionMode)
        || !sameSfcScopeProvenance(boundOptions.scopeProvenance, expectedProvenance)
        || !sfcOptionsStayWithinAxes(boundOptions, axes)) throw new Error('候选范围选项未与服务端创建的 scope、模型、项目和候选数量一致。')
      sfcScopeOptionsError.value = null
    }
    reloadSfcScope = async () => { try { await readScopeOptions() } catch { if (isCurrent() && preparedSfcScope?.key === scopeKey) sfcScopeOptionsError.value = '此候选范围选项读取失败，输入已保留；可重新读取。' } }
    await readScopeOptions()
    if (!isCurrent() || preparedSfcScope?.key !== scopeKey) return
    stage = 'prediction'
    const common = { project_id: expectedProjectId, schema_version: '1.1' as const, model_selection: modelSelection, structure_refs: primaryRefs }
     const modifierComponents = value.modifiers.map((item, index) => ({ structure_ref: modifierRefs[index]!, fraction: item.fraction }))
    setWorkspaceSubmissionLocked(expectedWorkspaceKey, true)
    const accepted = tool.id === 'single_column'
      ? await store.createSfc11({ ...common, chromatography_type: 'sfc', tool: 'single_column', input: {
        candidate_scope_id: scope.id, column_id: value.column_id!, modifier_components: modifierComponents,
        modifier_total_fraction: value.modifierTotalFraction, top_k: value.topK,
      } } as Sfc11SingleBody, isCurrent)
      : await store.createSfc11({ ...common, chromatography_type: 'sfc', tool: 'full_column', input: {
        candidate_scope_id: scope.id, modifier_components: modifierComponents,
        modifier_total_fraction: value.modifierTotalFraction, top_k: value.topK,
      } } as Sfc11FullBody, isCurrent)
    saveAcceptedForWorkspace(expectedWorkspaceKey, accepted.data)
    if (!isCurrent()) return
    await followPrediction(accepted.data.id, undefined, {
      model, toolId: tool.id, scopeId: scope.id, candidateCount: scope.candidate_count,
      expansionMode, provenance: expectedProvenance, selectionGeneration,
    })
  } catch (reason) {
    if (isCurrent()) {
      const message = reason instanceof Error ? reason.message : '请求失败。'
      if (stage === 'scope-options') sfcScopeOptionsError.value = `候选范围已创建，但选项核验失败；输入已保留。${message}`
      else if (stage === 'prediction') {
        const rejected = reason instanceof ApiClientError && reason.status >= 400 && reason.status < 500 && reason.status !== 408
        if (rejected) setWorkspaceSubmissionLocked(expectedWorkspaceKey, false)
        sfcSubmissionLocked.value = !rejected
        sfcPredictionError.value = rejected ? `预测提交被拒绝；输入已保留。${message}` : '预测提交结果未确认；输入已保留。请先到预测记录核对，当前不会重复提交。'
      } else {
        if (stage === 'scope' && !submissionWasRejected(reason)) sfcSubmissionLocked.value = true
        sfcCandidateError.value = reason instanceof ApiClientError && reason.code === 'SFC_CANDIDATE_SCOPE_EMPTY' ? '当前条件组合无匹配，请调整条件。' : reason instanceof ApiClientError && reason.code === 'SFC_CANDIDATE_SCOPE_LIMIT_EXCEEDED' ? '候选范围超过有效上限，请缩小条件范围。' : `候选范围校验失败；输入已保留。${message}`
      }
    }
  } finally { if (isCurrent()) submitting.value = false }
}

async function submitTlcComparison(conditions: readonly TlcInput[]) {
  if (submitting.value || submissionLocked.value) return
  if (serviceActionsUnavailable.value) { blockUnfrozenTool('连接不可用；当前不会提交 TLC 条件比较。'); return }
  if (replayRequested.value) { formError.value = '此重放草稿绑定 TLC 单次工具；当前不会切换工具或提交其他任务。'; return }
  const model = selectedModel.value
  const comparisonCapability = store.typeCapability?.tools.find((item) => item.id === 'condition_compare')
  if (!canCreatePredictions.value || !projectId.value || !store.toolSchema || store.toolSchema.health.status !== 'READY' || store.toolSchema.id !== 'condition_compare'
    || !comparisonCapability || comparisonCapability.health.status !== 'READY'
    || store.toolSchema.schema_version !== comparisonCapability.schema_version || !model || !rawStructureInputsReady.value) {
    blockUnfrozenTool('TLC 条件比较缺少预测权限、已验证项目、结构、模型或对应工具 schema。'); return
  }
  let modelSelection: PredictionCandidateExactModelSelection
  try { modelSelection = createExactModelSelection(model as PredictionCandidateModelOption, 'tlc') }
  catch (reason) { blockUnfrozenTool(reason instanceof CandidateModelSelectionError ? reason.message : 'TLC 模型身份无效；当前不会提交。'); return }
  const generation = projectGeneration
  const expectedProjectId = projectId.value
  const loadGeneration = loadRequestGeneration
  const attempt = ++submitAttemptGeneration
  const expectedModelKey = selectedModelKey.value
  const expectedWorkspaceGeneration = workspaceGeneration
  const expectedWorkspaceKey = activeWorkspace?.key
  const isCurrent = () => isCurrentProject(generation, expectedProjectId) && loadRequestGeneration === loadGeneration
    && store.selectedType === 'tlc' && store.selectedTool === 'condition_compare'
    && selectedModelKey.value === expectedModelKey && submitAttemptGeneration === attempt
    && workspaceGeneration === expectedWorkspaceGeneration && activeWorkspace?.key === expectedWorkspaceKey
  submitting.value = true
  const normalized = await normalizeStructureTargets(['primary'], new Map(structures.value[0] ? [['primary', structures.value[0]]] : []), 'tlc', expectedProjectId, isCurrent)
  if (attempt !== submitAttemptGeneration) return
  if (!normalized) { submitting.value = false; return }
  if (!isCurrent()) { formError.value = '项目、色谱类型或结构输入在校验过程中发生变化；本次比较已取消。'; submitting.value = false; return }
  const body: ContractTlcConditionComparisonBody = { project_id: expectedProjectId, schema_version: store.toolSchema.schema_version, model_selection: modelSelection, structure_ref: normalized.structures[0]!.id, conditions: [...conditions], chromatography_type: 'tlc', tool: 'condition_compare' }
  setWorkspaceSubmissionLocked(expectedWorkspaceKey, true)
  try { const accepted = await store.createTlcComparison(body, isCurrent); saveAcceptedForWorkspace(expectedWorkspaceKey, accepted.data); if (isCurrent()) await followPrediction(accepted.data.id) }
  catch (reason) { const rejected = submissionWasRejected(reason); if (rejected) setWorkspaceSubmissionLocked(expectedWorkspaceKey, false); if (isCurrent()) { submissionLocked.value = !rejected; formError.value = rejected ? (reason instanceof Error ? reason.message : 'TLC 条件比较提交失败') : 'TLC 条件比较提交状态未确认；请先核对预测记录，当前不会重复提交。' } }
  finally { if (isCurrent()) submitting.value = false }
}

async function chooseTlcTool(tool: TlcTool, fromRoute = false) {
  if (!visibleTlcTools.value.includes(tool)) return
  if (tool === 'batch' && openBatchPrediction()) return
  if (tool === 'reverse_solvent') { store.selectedTool = tool; void router.push('/prediction/tlc/reverse'); return }
  if (!fromRoute && route.path === '/prediction/tlc' && route.query.tool !== tool) void router.replace({ path: '/prediction/tlc', query: { ...route.query, tool } })
  const loadGeneration = ++tlcToolLoadGeneration
  if (tool === 'batch') {
    activeMode.value = 'batch'
    store.selectedTool = tool
    return
  }
  activeMode.value = 'single'
  store.selectedTool = tool
  const capability = store.typeCapability?.tools.find((item) => item.id === tool)
  if (!capability) { formError.value = '服务端未声明此 TLC 工具；当前不会提交。'; return }
  if (store.toolSchema?.id === tool && store.toolSchema.schema_version === capability.schema_version) return
  const generation = projectGeneration
  const requestGeneration = loadRequestGeneration
  const expectedProjectId = projectId.value
  const isCurrent = () => isCurrentProject(generation, expectedProjectId) && loadRequestGeneration === requestGeneration
    && store.selectedType === 'tlc' && store.selectedTool === tool && tlcToolLoadGeneration === loadGeneration
  formError.value = null
  try {
    await store.loadTool('tlc', tool, capability.schema_version, isCurrent)
    if (isCurrent() && (store.toolSchema?.id !== tool || store.toolSchema.schema_version !== capability.schema_version)) {
      formError.value = 'TLC 工具 schema 与当前选择不匹配；当前不会提交。'
    }
  } catch (reason) { if (isCurrent()) formError.value = reason instanceof Error ? reason.message : 'TLC 工具 schema 暂不可用。' }
}
function selectTlcToolbarTool(tool: TlcTool) {
  if (!visibleTlcTools.value.includes(tool)) return
  chooseTlcTool(tool)
}
function openBatchPrediction(): boolean {
  if (!activeProfile.predictionTools[store.selectedType]?.includes('batch')) return false
  persistWorkspace()
  const query: Record<string, string> = {}
  if (projectId.value) query.project_id = projectId.value
  if (store.selectedType === 'sfc' && ['single_column', 'full_column'].includes(sfcSelectedToolId.value)) query.from_tool = sfcSelectedToolId.value
  void router.push({ path: `/prediction/${store.selectedType}/batch`, query })
  return true
}
function selectBatchMode() {
  if (!openBatchPrediction()) activeMode.value = 'batch'
}
function onModelSelectionChanged() {
  selectionInvalidated.value = false
  selectionContext.value = { type: store.selectedType, projectId: projectId.value, modelKey: selectedModelKey.value }
  formError.value = null
}
function selectType(type: string) { void router.push(type === 'tlc' ? '/prediction/tlc' : `/prediction/${type}`) }
function loadTypesForView() {
  const generation = ++typesRequestGeneration
  return store.loadTypes(() => generation === typesRequestGeneration && viewInitialized)
}

onMounted(() => {
  if (!app.accessContext) void app.load()
  if (routeType.value) store.selectedType = routeType.value
  if (!routeType.value && !store.selectedType && visibleTypes.value.length) store.selectedType = visibleTypes.value[0].id
  viewInitialized = true
  void loadTypesForView().then(() => {
    if (projectId.value) void loadSelectedType()
  }).catch(() => undefined)
})
watch(() => app.state, (state) => {
  if (state === 'error') retryMetadataPending = true
  if (state !== 'ready' || !retryMetadataPending) return
  retryMetadataPending = false
  void loadTypesForView().then(() => {
    if (!store.typeCapability && !store.accepted && !store.prediction && projectId.value) void loadSelectedType()
  }).catch(() => undefined)
})
onUnmounted(() => { typesRequestGeneration += 1; projectGeneration += 1; submitAttemptGeneration += 1; loadRequestGeneration += 1; structureInputGeneration.value += 1; hplcRawStructures.value = []; stopPolling(); persistWorkspace() })
</script>

<template>
  <div class="view prediction-view">
    <header class="page-header"><div><span class="eyebrow">PREDICTION WORKBENCH</span><h1>色谱预测</h1><p>输入结构和实际实验条件，系统自动管理项目、结构引用、schema 和任务状态。当前构建 {{ activeProfile.id }}。</p></div></header>
    <section class="panel prediction-switcher">
      <div class="chromatography-tabs" role="tablist" aria-label="色谱类型"><button v-for="item in visibleTypes" :key="item.id" role="tab" :aria-selected="store.selectedType === item.id" :class="{ active: store.selectedType === item.id }" @click="selectType(item.id)">{{ item.display_name }}</button></div><div v-if="store.selectedType === 'tlc'" class="tlc-tool-tabs" role="tablist" aria-label="TLC 工具"><button v-for="tool in visibleTlcTools" :key="tool" type="button" role="tab" :aria-selected="store.selectedTool === tool" :class="{ active: store.selectedTool === tool }" @click="selectTlcToolbarTool(tool)">{{ tool === 'single' ? '单次预测' : tool === 'batch' ? '批量预测' : tool === 'condition_compare' ? '条件比较' : '反推溶剂' }}</button></div><div v-else class="segmented"><button :class="{ active: activeMode === 'single' }" @click="activeMode = 'single'">单次预测</button><button :class="{ active: activeMode === 'batch' }" @click="selectBatchMode">批量预测</button></div>
    </section>
    <OfflinePredictionForm :type="store.selectedType || visibleTypes[0]?.id || 'tlc'" :offline="offlinePreview" />
    <section v-if="app.accessContext && projectState !== 'blocked'" class="panel project-section"><label>项目<select v-model="selectedProjectId" data-testid="prediction-project"><option value="" disabled>选择服务端访问上下文中的项目</option><option v-for="project in availableProjects" :key="project.id" :value="project.id">{{ project.name || project.id }}</option></select></label><p v-if="projectState === 'needs_selection'" class="error-text">请选择项目；不会使用未验证的默认项目。</p></section>
    <div class="prediction-main-grid">
      <div class="prediction-input-column">
    <section v-if="projectContextPending" class="panel placeholder" role="status" aria-live="polite">正在读取访问上下文；本地页面可浏览，预测暂不可提交。</section>
    <section v-else-if="projectContextFailed" class="panel error-text">访问上下文读取失败；预测暂不可提交。{{ app.error }}</section>
    <section v-else-if="projectState === 'blocked'" class="panel error-text">服务端没有为当前访问上下文分配可访问项目，预测暂不可提交。</section>
    <section v-else-if="projectState === 'needs_selection'" class="panel error-text">当前访问上下文需要先选择项目，预测暂不可提交。</section>
    <section v-else-if="!canCreatePredictions && !replayRequested" class="panel error-text" data-testid="prediction-permission-blocked">当前访问上下文没有预测创建权限；提交已禁用。</section>
      <template v-else-if="activeMode === 'single' && store.selectedType">
      <section v-if="selectedUnavailable" class="panel empty-state"><span class="eyebrow">{{ selectedTypeLabel }}</span><h2>服务端能力不可用</h2><p>当前状态：{{ selectedHealth?.status ?? 'UNKNOWN' }}</p><p v-if="selectedUnavailableReason" class="error-text">原因：{{ selectedUnavailableReason }}</p><p v-if="formError" role="alert" class="error-text">{{ formError }}</p><p>提交已禁用；请等待服务端能力恢复后重试。</p></section>
      <template v-else>
      <p v-if="serviceActionsUnavailable" class="panel warning-text">连接不可用；结构规范化、候选预检和预测提交已暂停。已提交任务的状态请按原任务 ID核实。</p>
       <StructureWorkspace :context="structureWorkspace">
      <section class="panel structure-section"><div class="section-heading"><div><span class="eyebrow">MOLECULE INPUT</span><h2>{{ store.selectedType === 'cc' ? '两个待分离结构' : requiredStructures === 2 ? '两个对映体结构' : '分子结构' }}</h2></div><p>点击预测时自动进行服务端结构校验，也可提前校验；校验成功后保留立体信息并提交结构引用。</p></div><div v-if="replayDraft" class="replay-structure-refs" data-testid="replay-structure-refs"><p>沿用服务端草稿中按原顺序绑定的结构快照：</p><article v-for="(item, index) in replayStructureSnapshots" :key="item.id"><strong>{{ index + 1 }} · {{ item.id }} · {{ item.stereochemistry_status }}</strong><code>{{ item.canonical_smiles }}</code></article><template v-if="replayDraft.modifier_structure_snapshots?.length"><p>SFC Modifier 结构快照：</p><article v-for="(item, index) in replayDraft.modifier_structure_snapshots" :key="`modifier:${item.id}`"><strong>{{ index + 1 }} · {{ item.id }} · {{ item.stereochemistry_status }}</strong><code>{{ item.canonical_smiles }}</code></article></template></div><div v-else class="grid" :class="{ 'two-column': requiredStructures === 2 }"><StructureInput :key="`${projectId}:${store.selectedType}:${workspaceGeneration}:${structureInputGeneration}`" target-id="primary" :initial-smiles="restoredSmiles[0] ?? ''" :label="requiredStructures === 2 ? '结构 1' : '分子结构'" :example-smiles="store.selectedType === 'tlc' ? 'O=C(C1=CC=CC=C1)CCCl' : store.selectedType === 'cc' ? 'Brc1ccc2ccccc2c1' : store.selectedType === 'gc' ? 'CCCCCCC' : 'C[C@H](N)C(=O)O'" :project-id="projectId" :disabled="serviceActionsUnavailable" @draft-change="updateStructureDraft(0, $event)" @normalized="onNormalized($event, 0)" @normalized-with-raw="onHplcNormalizedWithRaw($event, 0)" @invalidated="invalidateStructureSlot(0)" /><StructureInput v-if="requiredStructures === 2" :key="`${projectId}:${store.selectedType}:${workspaceGeneration}:${structureInputGeneration}:second`" target-id="secondary" :initial-smiles="restoredSmiles[1] ?? ''" label="结构 2" :example-smiles="store.selectedType === 'cc' ? 'COC(=O)c1ccc(Cl)cc1' : 'C[C@@H](N)C(=O)O'" :project-id="projectId" :disabled="serviceActionsUnavailable" @draft-change="updateStructureDraft(1, $event)" @normalized="onNormalized($event, 1)" @normalized-with-raw="onHplcNormalizedWithRaw($event, 1)" @invalidated="invalidateStructureSlot(1)" /></div></section>
      <SharedKetcherCanvas />
      <section v-if="store.selectedType !== 'sfc'" class="panel model-strip"><label>预测模型<select v-model="selectedModelKey" @change="onModelSelectionChanged"><option value="" disabled>请选择健康模型</option><option v-for="model in healthyModels" :key="modelOptionKey(model)" :value="modelOptionKey(model)">{{ model.task_type }} · {{ model.version }} · {{ model.scope }}</option></select></label><p v-if="!healthyModels.length" class="error-text">服务端未返回可用真实模型。</p><p v-if="selectedModelBinding.error" class="error-text" data-testid="model-selection-error">{{ selectedModelBinding.error }}</p></section>
       <CcSingleForm v-if="store.selectedType === 'cc'" :key="`${projectId}:${replayDraft?.id ?? 'new'}:${workspaceGeneration}`" :capability="ccFormCapability" :initial-value="replayDraft ? replayInput as unknown as CcFormValue : formDraft as CcFormValue | null" :busy="submitting" :disabled="serviceActionsUnavailable || submissionLocked || !canCreatePredictions || !structureReady || replayLoading || Boolean(replayRequested && (!replayDraft || !selectedModel)) || !selectedModelBinding.selection" @update:value="formDraft = $event" @submit="submitInput" />
       <HplcSingleForm v-else-if="store.selectedType === 'hplc' && (!replayDraft || replayUiSupported)" :key="`${projectId}:${replayDraft?.id ?? 'new'}:${workspaceGeneration}`" :columns="hplcColumns" :initial-value="replayDraft ? replayInput as unknown as HplcFormValue : formDraft as HplcFormValue | null" :busy="submitting" :disabled="serviceActionsUnavailable || submissionLocked || !canCreatePredictions || !structureReady || replayLoading || Boolean(replayRequested && (!replayDraft || !replayModelMatches)) || !selectedModelBinding.selection" @update:value="formDraft = $event" @submit="submitInput" />
      <section v-else-if="store.selectedType === 'hplc' && replayDraft" class="panel warning-text" data-testid="hplc-replay-readonly"><strong>此 HPLC 草稿只读</strong><p>{{ replayDraft.schema_version === '1.0' ? '1.0 历史草稿没有提交时的原始 SMILES，不能安全重放。' : '草稿缺少可验证的 1.1 原始输入或完整模型预处理身份，不能安全重放。' }}</p></section>
       <GcSingleForm v-else-if="store.selectedType === 'gc' && (!replayDraft || replayUiSupported)" :key="`${projectId}:${replayDraft?.id ?? 'new'}:${workspaceGeneration}`" :capability="gcFormCapability" :initial-value="replayDraft ? replayInput as GcFormValue : formDraft as GcFormDraftValue | null" :busy="submitting" :disabled="serviceActionsUnavailable || submissionLocked || !gcCapabilityReady || !canCreatePredictions || !structureReady || replayLoading || Boolean(replayRequested && (!replayUiSupported || !replayModelMatches))" @update:value="formDraft = $event" @submit="submitInput" />
      <section v-else-if="store.selectedType === 'gc' && replayDraft" class="panel warning-text" data-testid="gc-replay-readonly"><strong>此 GC 草稿只读</strong><p>GC 1.0 历史草稿或缺少完整 profile 的草稿仅保留原输入；不会补填 profile、切换模型或钳制温度。请在当前 READY GC 1.1 能力下创建新预测。</p></section>
       <TlcToolPanel v-else-if="store.selectedType === 'tlc' && tlcOptionsReady" :key="workspaceGeneration" :model-value="(store.selectedTool as TlcTool)" :initial-draft="tlcWorkspaceDraft" :initial-input="replayDraft ? replayInput as TlcInput : null" :visible-tools="replayDraft ? ['single'] : visibleTlcTools" :busy="serviceActionsUnavailable || submitting || submissionLocked || !canCreatePredictions || !structureReady || replayLoading || Boolean(replayRequested && (!replayDraft || !selectedModel)) || (store.selectedTool === 'condition_compare' && store.toolSchema?.id !== 'condition_compare')" :structure-ref="replayDraft ? replayStructureIds[0] ?? null : structures[0]?.id ?? null" :structure-input-ready="Boolean(replayDraft || rawStructureInputsReady)" :hide-navigation="true" :solvent-options="tlcSolventOptions" :candidate-systems="tlcCandidateSystems" :reverse-range="tlcReverseRange" :points="tlcReverseCurve.points" :initial-rf="tlcReverseCurve.initialRf" :result-state="tlcReverseCurve.resultState" :failed-point-count="tlcReverseCurve.failedPointCount" :result-message="tlcReverseCurve.resultMessage" @update:model-value="chooseTlcTool" @draft-change="tlcWorkspaceDraft = $event; formDraft = $event" @submit-single="submitInput" @request-batch-input="blockUnfrozenTool('TLC 批量输入制品契约尚未冻结，当前不会提交。')" @submit-batch="blockUnfrozenTool('TLC 批量提交契约尚未冻结，当前不会提交。')" @submit-comparison="submitTlcComparison" />
      <div v-else-if="store.selectedType === 'sfc' && !replayDraft" class="sfc-workspace">
         <SfcSearchForm :key="`${projectId}:${sfcSelectedToolId}:${workspaceGeneration}`" :project-id="projectId" :tools="sfcSearchTools" :selected-tool-id="sfcSelectedToolId" :models="sfcSearchModels" :selected-model="sfcSelectedModelChoice" :options-state="sfcOptionsState" :options="sfcSearchOptions" :options-error="sfcOptionsError" :candidate-error="sfcCandidateError" :scope-options-error="sfcScopeOptionsError" :prediction-error="sfcPredictionError" :prediction-status-error="sfcPredictionStatusError" :accepted-prediction-id="sfcAcceptedId" :submission-locked="sfcSubmissionLocked" :candidate-preview-text="sfcPreviewText" :preview-busy="sfcPreviewBusy" :candidate-preview-blocked="Boolean(sfcPreview && sfcPreview.status !== 'READY')" :initial-draft="sfcWorkspaceDraft" :structures-ready="structureReady" :submitting="submitting" :disabled="sfcFormDisabled || submissionLocked" @tool-change="selectSfcTool" @model-change="selectSfcModel" @axes-change="invalidateSfcCandidates" @draft-change="sfcWorkspaceDraft = $event; formDraft = $event" @preview="previewSfcCandidates" @retry-scope-options="retrySfcScopeOptions" @retry-prediction-status="retrySfcPredictionStatus" @submit="submitSfc" />
        <p v-if="!sfcSearchTools.some((item) => item.health === 'READY')" class="panel warning-text" data-testid="sfc-no-ready-tool">当前构建没有 READY 的 SFC 搜索工具；服务端不可用原因会保留显示，提交已禁用。</p>
        <p v-else-if="sfcSelectedToolChoice?.health === 'READY' && !sfcCapabilityReady && sfcOptionsState !== 'error'" class="panel warning-text" data-testid="sfc-schema-not-ready">当前 SFC 工具 schema 或搜索约束尚未形成完整 READY 绑定。</p>
      </div>
      <section v-else-if="store.selectedType === 'sfc' && replayDraft" class="panel warning-text" data-testid="sfc-replay-readonly"><strong>SFC 草稿只读</strong><p>{{ replayDraft.schema_version === '1.0' ? '1.0 历史草稿保持原输入与结构引用；不会补候选范围、替换模型或重新解释旧条件。请创建新的 SFC 1.1 搜索。' : '当前仅恢复草稿的冻结输入和结构快照；不会自动创建候选范围或绑定当前模型。请创建新的 SFC 1.1 搜索。' }}</p></section>
      <section v-else class="panel empty-state"><span class="eyebrow">{{ store.selectedType.toUpperCase() }}</span><h2>服务端能力尚未就绪</h2><p>{{ store.selectedType === 'sfc' ? 'SFC 候选范围字段尚未冻结或服务端未返回完整能力；当前不会提交不完整字段。' : store.selectedType === 'tlc' ? 'TLC 条件选项尚未由服务端返回；当前不会提交静态候选值。' : '该工具的服务端输入契约尚未就绪。' }}</p></section>
      </StructureWorkspace>
       </template>
    </template>
    <BatchUnavailable v-else-if="activeMode === 'batch'" :type-label="selectedTypeLabel" />
    <p v-if="replayLoading" class="panel placeholder" role="status">正在从服务端恢复重放草稿…</p><section v-if="replayDraft" class="panel replay-notice" data-testid="replay-draft-status"><button type="button" class="secondary" data-testid="new-prediction" :disabled="submitting || replayLoading || submissionLocked" @click="startNewPrediction">新建预测</button><p>正在查看服务端草稿 {{ replayDraft.id }}（模型 {{ replayDraft.model_selection.model_id }} · {{ replayDraft.model_selection.model_version }}）；编辑仅保留在当前页面，刷新后会重新从服务端读取草稿。</p><p v-if="!canCreatePredictions" class="warning-text">当前访问上下文没有预测创建权限；草稿只读。</p><section v-if="replayDraft.chromatography_type === 'hplc' && hplcReplayInput" class="hplc-input-snapshot" data-testid="hplc-replay-input"><h3>HPLC 输入快照 · {{ hplcReplayInput.version }}</h3><dl><div><dt>色谱柱</dt><dd>{{ hplcReplayInput.input.column_ids.join('、') }}</dd></div><div><dt>流速</dt><dd>{{ hplcReplayInput.input.flow_rate_ml_min }} mL/min</dd></div><div><dt>异丙醇比例</dt><dd>{{ hplcReplayInput.input.isopropanol_fraction }}</dd></div></dl><template v-if="hplcReplayInput.version === '1.1'"><div v-for="(smiles, index) in hplcReplayInput.input.model_input_smiles" :key="index"><strong>本次原始 SMILES {{ index + 1 }}</strong><code>{{ smiles }}</code></div></template><p v-else class="warning-text">历史 1.0 草稿没有保存本次原始 SMILES，规范化结构不能替代原始输入。</p></section><details><summary>草稿输入快照</summary><pre>{{ JSON.stringify(replayDraft.input, null, 2) }}</pre></details></section><p v-if="replayDraft && !replayUiSupported && replayDraft.chromatography_type !== 'hplc'" class="panel warning-text" data-testid="replay-draft-unsupported">{{ replayDraft.chromatography_type === 'gc' ? 'GC 1.0 历史或缺少 profile 的草稿只读保留；不会补值、钳制或自动切换模型。' : '此工具暂不能安全加载为可编辑表单；草稿可查看，提交已停用。' }}</p><p v-else-if="replayRequested && !replayLoading && !replayDraft" class="panel warning-text" data-testid="replay-draft-blocked">重放草稿未通过校验；表单提交已停用。</p><p v-if="formError" class="panel error-text" role="alert">{{ formError }}</p><p v-if="submissionLocked && !store.accepted && !store.prediction" class="panel warning-text" data-testid="submission-status-unknown">上次提交状态尚未确认，请先到预测记录核对；不会自动重复提交。</p>
      </div>
    <section class="panel result-panel" :class="{ 'sfc-result-panel': store.prediction?.chromatography_type === 'sfc' }"><div class="section-heading"><div><span class="eyebrow">REAL RESULT</span><h2>预测结果</h2></div><span v-if="store.prediction" class="runtime-pill">{{ resultStatusLabel }}</span></div><div v-if="store.accepted && !store.prediction" class="placeholder">任务已提交，正在等待服务端结果…</div><div v-else-if="!store.prediction" class="placeholder">完成输入后提交预测；这里不会展示演示成功数据。</div><template v-else><dl v-if="!resultError" class="facts"><div><dt>类型 / 工具</dt><dd>{{ store.prediction.chromatography_type.toUpperCase() }} / {{ store.prediction.tool }}</dd></div><div><dt>模型版本</dt><dd>{{ store.prediction.model.version }}</dd></div><div><dt>数据来源</dt><dd>{{ store.prediction.data_origin }}</dd></div><div><dt>更新时间</dt><dd>{{ store.prediction.updated_at }}</dd></div></dl><section v-if="hplcPredictionInput" class="hplc-input-snapshot" data-testid="hplc-result-input"><h3>HPLC 输入快照 · {{ hplcPredictionInput.version }}</h3><dl><div><dt>色谱柱</dt><dd>{{ hplcPredictionInput.input.column_ids.join('、') }}</dd></div><div><dt>流速</dt><dd>{{ hplcPredictionInput.input.flow_rate_ml_min }} mL/min</dd></div><div><dt>异丙醇比例</dt><dd>{{ hplcPredictionInput.input.isopropanol_fraction }}</dd></div></dl><template v-if="hplcPredictionInput.version === '1.1'"><div v-for="(smiles, index) in hplcPredictionInput.input.model_input_smiles" :key="index"><strong>本次原始 SMILES {{ index + 1 }}</strong><code>{{ smiles }}</code></div></template><p v-else class="warning-text">历史 1.0 记录没有保存本次原始 SMILES；此处保留规范化结构快照，不从结构反推原始表示。</p><div v-if="hplcPredictionInput.version === '1.0'" class="normalized-structures"><strong>服务端规范化结构</strong><code v-for="(structure, index) in hplcPrediction?.input_snapshot.normalized_structures ?? []" :key="index">{{ index + 1 }} · {{ structure }}</code></div></section><p v-if="store.prediction.error" class="error-text">{{ store.prediction.error.message }}</p><p v-if="resultError" class="error-text">{{ resultError }}</p><PredictionHighlights v-if="store.prediction.chromatography_type !== 'sfc'" :prediction="store.prediction" :hide-invalid="Boolean(resultError)" /><p v-if="sfcSupportedScope" class="hint" data-testid="sfc-supported-scope">适用范围：{{ sfcSupportedScope }}</p><dl v-if="sfcResultCounts" class="facts sfc-result-counts" data-testid="sfc-result-counts"><div><dt>冻结候选范围</dt><dd>{{ sfcResultCounts.frozenScopeCount ?? '未知' }}</dd></div><div><dt>实际评分候选</dt><dd>{{ sfcResultCounts.evaluatedCount ?? '未知' }}</dd></div><div><dt>实际返回条数</dt><dd>{{ sfcResultCounts.returnedCount }}</dd></div></dl><p v-if="sfcResultCounts?.topK" class="hint" data-testid="sfc-top-k-ceiling">TopK 上限：{{ sfcResultCounts.topK }} 条；它表示返回条数上限。</p><p v-if="sfcEmptyResult" class="empty-state" data-testid="sfc-empty-result">服务端未返回候选结果。</p><div v-if="reverseUnreachable" class="empty-state"><strong>目标 Rf 不可达</strong><p>服务端未返回满足目标范围的候选溶剂体系。</p></div><div v-if="store.prediction.chromatography_type === 'sfc' && !resultError && resultRows.length" class="table-wrap"><table><thead><tr><th v-for="field in resultFields" :key="field">{{ resultFieldLabel(field) }}</th></tr></thead><tbody><tr v-for="(row, index) in resultRows" :key="index"><td v-for="field in resultFields" :key="field">{{ formatResultCell(row[field], field) }}</td></tr></tbody></table></div><section v-if="sfcApplicabilityWarnings.length" class="event-list" data-testid="sfc-applicability-warnings"><h3>SFC 适用范围提示</h3><p v-for="(warning, index) in sfcApplicabilityWarnings" :key="`${warning.code}:${index}`"><strong>{{ warning.code }}：</strong>{{ warning.message }}</p></section><details v-if="store.prediction.chromatography_type !== 'sfc'" class="prediction-detail-table"><summary>完整结果表与 CC 指标</summary><div v-if="!resultError && resultRows.length" class="table-wrap"><table><thead><tr><th v-for="field in resultFields" :key="field">{{ resultFieldLabel(field) }}</th></tr></thead><tbody><tr v-for="(row, index) in resultRows" :key="index"><td v-for="field in resultFields" :key="field">{{ formatResultCell(row[field], field) }}</td></tr></tbody></table></div><section v-if="ccResultReady" class="cc-result-metrics" data-testid="cc-result-metrics"><h3>CC 洗脱指标</h3><p class="hint">按服务端正式指标原样展示；模型返回体积不在前端重算。</p><p v-if="ccMetricDataInvalid" class="error-text" data-testid="cc-result-metrics-invalid">服务端 CC 指标字段不符合公开结果 DTO，已隐藏指标表。</p><div v-else-if="ccMetricRows.length" class="table-wrap"><table><thead><tr><th>指标</th><th>值</th><th>单位</th><th>来源</th></tr></thead><tbody><tr v-for="(metric, index) in ccMetricRows" :key="`${metric.label}:${index}`"><td>{{ metric.label }}</td><td>{{ formatResultCell(metric.value, metric.key) }}</td><td>{{ metric.unit }}</td><td>{{ metric.origin }}</td></tr></tbody></table></div><p v-else class="muted" data-testid="cc-result-metrics-empty">服务端未返回 CC 指标。</p></section></details><section v-if="ccApplicabilityWarnings.length" class="event-list" data-testid="cc-applicability-warnings"><p v-for="(warning, index) in ccApplicabilityWarnings" :key="`${warning.code}:${index}`"><strong>{{ warning.code }}：</strong>{{ warning.message }}</p></section><section v-if="hplcApplicabilityWarnings.length" class="event-list" data-testid="hplc-applicability-warnings"><p v-for="(warning, index) in hplcApplicabilityWarnings" :key="`${warning.code}:${index}`"><strong>{{ warning.code }}：</strong>{{ warning.message }}</p></section><section v-if="gcApplicabilityWarnings.length" class="event-list" data-testid="gc-applicability-warnings"><h3>GC 适用范围与实验提示</h3><p v-for="(warning, index) in gcApplicabilityWarnings" :key="`${warning.code}:${index}`"><strong>{{ warning.code }}：</strong>{{ warning.message }}</p></section><div v-if="visiblePredictionWarnings.length" class="event-list"><p v-for="warning in visiblePredictionWarnings" :key="warning.code">{{ warning.message }}</p></div><div v-if="resultWarnings.length" class="event-list"><p v-for="warning in resultWarnings" :key="warning">{{ warning }}</p></div></template></section>
    </div>
  </div>
</template>

<style scoped>
.tlc-tool-tabs { display: flex; flex-wrap: wrap; gap: 8px; }
.tlc-tool-tabs button { border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; background: #fff; color: var(--primary-deep); font: inherit; cursor: pointer; }
.tlc-tool-tabs button.active { background: var(--primary); color: #fff; }
.tlc-tool-tabs button:disabled { cursor: not-allowed; opacity: .55; }
.sfc-workspace { display: grid; align-content: start; gap: 16px; min-width: 0; }
.sfc-modifier-section { display: grid; gap: 10px; }
.sfc-modifier-section > p { margin: 0; color: #6b8193; font-size: 13px; line-height: 1.5; }
.sfc-modifier-section > [data-testid="sfc-modifier-structure"] { min-width: 0; }
.replay-structure-refs { display: grid; gap: 10px; min-width: 0; }
.replay-structure-refs article { display: grid; gap: 6px; min-width: 0; border: 1px solid #d8e5ef; border-radius: 9px; padding: 10px; }
.replay-structure-refs code { white-space: pre-wrap; overflow-wrap: anywhere; }
.replay-notice pre { max-width: 100%; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
.hplc-input-snapshot { display: grid; gap: 8px; min-width: 0; margin-block: 12px; }
.hplc-input-snapshot dl { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr)); gap: 8px; margin: 0; }
.hplc-input-snapshot dl > div { min-width: 0; }
.hplc-input-snapshot code { display: block; white-space: pre-wrap; overflow-wrap: anywhere; }
.sfc-result-panel .table-wrap { width: 100%; max-width: 100%; overflow-x: auto; }
</style>
