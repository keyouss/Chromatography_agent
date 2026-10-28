<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import StructureInput from '@/components/StructureInput.vue'
import type { Structure } from '@/core/types'
import { SFC_CANDIDATE_KEYS, type SfcCandidateKey } from './types'
import type { SfcSearchBindingOptions, SfcSearchFormValue, SfcSearchModelChoice, SfcSearchToolChoice } from './SfcSearchForm.types'
import type { SfcSearchExpansionMode } from '@/shared/prediction/sfc-search.types'

const props = withDefaults(defineProps<{
  projectId: string
  tools: readonly SfcSearchToolChoice[]
  selectedToolId: string
  models: readonly SfcSearchModelChoice[]
  selectedModel: SfcSearchModelChoice | null
  optionsState: 'idle' | 'loading' | 'ready' | 'error'
  options: SfcSearchBindingOptions | null
  optionsError?: string | null
  candidateError?: string | null
  scopeOptionsError?: string | null
  predictionError?: string | null
  predictionStatusError?: string | null
  acceptedPredictionId?: string | null
  submissionLocked?: boolean
  candidatePreviewText?: string | null
  previewBusy?: boolean
  candidatePreviewBlocked?: boolean
  initialDraft?: SfcSearchWorkspaceDraft | null
  structuresReady?: boolean
  submitting?: boolean
  disabled?: boolean
}>(), { optionsError: null, structuresReady: true, submitting: false, disabled: false })

const emit = defineEmits<{
  'tool-change': [toolId: string]
  'model-change': [selection: { modelId: string | null; modelVersion: string | null }]
  submit: [value: SfcSearchFormValue]
  'retry-scope-options': []
  'retry-prediction-status': []
  preview: [axes: Record<SfcCandidateKey, (string | number)[]>, mode: SfcSearchExpansionMode]
  'axes-change': []
  'draft-change': [draft: SfcSearchWorkspaceDraft]
}>()

type ModifierDraft = { key: number; structure: Structure | null; rawSmiles: string; fraction: number | null }
export type SfcSearchWorkspaceDraft = {
  axes: Partial<Record<SfcCandidateKey, (string | number)[]>>
  groupColumnId: string
  expansionMode: SfcSearchExpansionMode
  topK: number | null
  totalFraction: number | null
  normalizeFractions: boolean
  modifiers: Array<{ smiles: string; fraction: number | null }>
}
const toolId = ref(props.selectedToolId)
const modelKey = ref(modelIdentityKey(props.selectedModel))
const expansionMode = ref<SfcSearchExpansionMode>('observed_distinct_rows')
const thresholdFilter = ref('')
const groupFilter = ref('')
const selectedGroupColumnId = ref('')
const topK = ref<number | null>(null)
const totalFraction = ref<number | null>(null)
const normalizeFractions = ref(false)
const modifierDrafts = ref<ModifierDraft[]>([])
const selectedAxisIndexes = reactive<Record<SfcCandidateKey, string[]>>(emptyAxisIndexes())
const pendingAxisIndex = reactive<Record<SfcCandidateKey, string>>(Object.fromEntries(SFC_CANDIDATE_KEYS.map((key) => [key, ''])) as Record<SfcCandidateKey, string>)
let modifierKey = 0
let restoreApplied = false

function modelIdentityKey(model: Pick<SfcSearchModelChoice, 'id' | 'version'> | null | undefined): string {
  return model ? JSON.stringify([model.id, model.version]) : ''
}

function emptyAxisIndexes(): Record<SfcCandidateKey, string[]> {
  return Object.fromEntries(SFC_CANDIDATE_KEYS.map((key) => [key, []])) as unknown as Record<SfcCandidateKey, string[]>
}

function clearSearchDraft() {
  for (const key of SFC_CANDIDATE_KEYS) selectedAxisIndexes[key] = []
  for (const key of SFC_CANDIDATE_KEYS) pendingAxisIndex[key] = ''
  selectedGroupColumnId.value = ''
  topK.value = null
  totalFraction.value = null
  normalizeFractions.value = false
  modifierDrafts.value = []
}

function resetExpansionMode() {
  if (expansionMode.value === 'observed_distinct_rows') return
  expansionMode.value = 'observed_distinct_rows'
  emit('axes-change')
}

function modelFingerprint(model: SfcSearchModelChoice | null): string {
  if (!model) return ''
  return JSON.stringify([
    model.id, model.version, model.health, model.newRequestReady, model.inputSchemaVersion, model.unavailableReason,
    model.business_scope_id, model.threshold, model.model_scope,
    model.group_id ?? null, model.group_label ?? null, model.stationary_phase ?? null, model.mode ?? null,
    model.allowed_columns ?? null,
  ])
}

watch(() => props.selectedToolId, (next) => {
  toolId.value = next
  modelKey.value = ''
  thresholdFilter.value = ''
  groupFilter.value = ''
  clearSearchDraft()
  resetExpansionMode()
  emit('model-change', { modelId: null, modelVersion: null })
})
watch(() => props.projectId, () => {
  modelKey.value = ''
  clearSearchDraft()
  resetExpansionMode()
  emit('model-change', { modelId: null, modelVersion: null })
})
watch(() => JSON.stringify([modelIdentityKey(props.selectedModel), modelFingerprint(props.selectedModel)]), () => {
  modelKey.value = modelIdentityKey(props.selectedModel)
  clearSearchDraft()
  resetExpansionMode()
})
watch(() => props.models.map((model) => [modelIdentityKey(model), modelFingerprint(model)]).sort(([a], [b]) => String(a).localeCompare(String(b))), (next, previous) => {
  if (!modelKey.value) return
  const current = next.find(([key]) => key === modelKey.value)?.[1]
  const prior = previous.find(([key]) => key === modelKey.value)?.[1]
  if (current === prior) return
  modelKey.value = ''
  thresholdFilter.value = ''
  groupFilter.value = ''
  clearSearchDraft()
  resetExpansionMode()
  emit('model-change', { modelId: null, modelVersion: null })
})

const selectedTool = computed(() => props.tools.find((tool) => tool.id === toolId.value) ?? null)
const compatibleModels = computed(() => props.models.filter((model) => selectedTool.value
  && ((selectedTool.value.requiresColumn && model.model_scope === 'single_column')
    || (!selectedTool.value.requiresColumn && model.model_scope === 'all_column'))))
const thresholdChoices = computed(() => [...new Set(compatibleModels.value
  .map((model) => model.threshold)
  .filter((value): value is string | number => value !== null && value !== undefined)
  .map((value) => String(value)))])
const groupChoices = computed(() => {
  const byId = new Map<string, string>()
  for (const model of compatibleModels.value) {
    if (model.model_scope === 'single_column' && model.group_id?.trim()) {
      byId.set(model.group_id, model.group_label?.trim() || model.group_id)
    }
  }
  return [...byId.entries()].map(([id, label]) => ({ id, label }))
})
const modelChoiceOptions = computed(() => compatibleModels.value.filter((model) =>
  (!thresholdFilter.value || String(model.threshold ?? '') === thresholdFilter.value)
  && (!groupFilter.value || model.group_id === groupFilter.value)))
const selectableModelChoices = computed(() => modelChoiceOptions.value.filter(isSelectableModel))
const selectedModel = computed(() => modelChoiceOptions.value.find((model) => modelIdentityKey(model) === modelKey.value) ?? null)
const selectionAcknowledged = computed(() => selectedTool.value?.id === props.selectedToolId
  && modelIdentityKey(props.selectedModel) === modelKey.value
  && modelChoiceOptions.value.some((model) => modelIdentityKey(model) === modelKey.value))

function isSelectableModel(model: SfcSearchModelChoice): boolean {
  if (model.health !== 'READY' || model.newRequestReady === false || !model.id || !model.version
    || model.threshold === null || model.threshold === undefined
    || (typeof model.threshold === 'number' && !Number.isFinite(model.threshold))
    || (typeof model.threshold === 'string' && !model.threshold.trim()) || model.model_scope === null) return false
  if (model.model_scope === 'single_column') {
    return Boolean(model.group_id?.trim() && model.group_label?.trim() && model.stationary_phase?.trim() && model.mode?.trim()
      && Array.isArray(model.allowed_columns) && model.allowed_columns.length > 0
      && model.allowed_columns.every((column) => typeof column === 'string' && column.trim().length > 0)
      && new Set(model.allowed_columns).size === model.allowed_columns.length)
  }
  return model.model_scope === 'all_column'
}

function selectUniqueReadyModel() {
  const previousKey = modelKey.value
  if (selectableModelChoices.value.some((model) => modelIdentityKey(model) === modelKey.value)) return
  const [model] = selectableModelChoices.value
  if (selectableModelChoices.value.length !== 1 || !model) {
    modelKey.value = ''
    if (previousKey !== modelKey.value) resetExpansionMode()
    emit('model-change', { modelId: null, modelVersion: null })
    return
  }
  modelKey.value = modelIdentityKey(model)
  if (previousKey !== modelKey.value) resetExpansionMode()
  emit('model-change', { modelId: model.id, modelVersion: model.version })
}

function validAxes(axes: readonly SfcSearchBindingOptions['axes'][number][] | undefined): boolean {
  if (!axes || axes.length !== SFC_CANDIDATE_KEYS.length) return false
  const byKey = new Map(axes.map((axis) => [axis.key, axis]))
  return byKey.size === SFC_CANDIDATE_KEYS.length && SFC_CANDIDATE_KEYS.every((key) => {
    const axis = byKey.get(key)
    return Boolean(axis && axis.label.trim() && (axis.unit === null || typeof axis.unit === 'string')
      && axis.values.length > 0
      && axis.values.every((value) => typeof value === 'string' ? value.trim().length > 0 : Number.isFinite(value)))
  })
}

function validLimits(limits: SfcSearchBindingOptions['limits'] | undefined): boolean {
  if (!limits) return false
  const finite = Object.values(limits).every((value) => typeof value === 'number' && Number.isFinite(value))
  return finite
    && Number.isInteger(limits.candidateLimit) && limits.candidateLimit > 0
    && Number.isInteger(limits.topKMinimum) && Number.isInteger(limits.topKMaximum) && limits.topKMinimum >= 1 && limits.topKMaximum >= limits.topKMinimum
    && Number.isInteger(limits.modifierMinimum) && Number.isInteger(limits.modifierMaximum) && limits.modifierMinimum >= 1 && limits.modifierMaximum >= limits.modifierMinimum
    && limits.componentFractionMinimum >= 0 && limits.componentFractionMaximum <= 1 && limits.componentFractionMaximum >= limits.componentFractionMinimum
    && limits.totalFractionMinimum >= 0 && limits.totalFractionMaximum <= 1 && limits.totalFractionMaximum >= limits.totalFractionMinimum
}

const bindingMatches = computed(() => {
  const model = selectedModel.value
  const options = props.options
  const tool = selectedTool.value
  if (!props.projectId || !tool || tool.health !== 'READY'
    || !model || !isSelectableModel(model) || !selectionAcknowledged.value
    || props.optionsState !== 'ready' || !options) return false
  return options.projectId === props.projectId && options.toolId === tool.id
    && options.modelId === model.id && options.modelVersion === model.version
    && options.business_scope_id === model.business_scope_id
    && options.model_scope === model.model_scope
    && options.group_id === (model.model_scope === 'single_column' ? model.group_id : null)
    && Object.is(options.threshold, model.threshold)
    && validAxes(options.axes) && validLimits(options.limits)
    && tool.requiresColumn === (model.model_scope === 'single_column')
    && (model.model_scope !== 'single_column' || Boolean(model.allowed_columns?.length))
})
const bindingError = computed(() => props.optionsState === 'ready' && Boolean(props.options)
  && Boolean(selectedModel.value) && selectionAcknowledged.value && !bindingMatches.value)
const supportsCoupledExpansion = computed(() => props.options?.supportedExpansionModes?.includes('coupled_discrete_conditions') ?? false)

const canEdit = computed(() => bindingMatches.value && !props.disabled && !props.submitting)
const structureInputDisabled = computed(() => !bindingMatches.value || props.disabled)
const formControlsDisabled = computed(() => props.disabled || props.submitting)
const axisOptions = computed(() => props.options?.axes ?? [])
const allowed_columns = computed(() => {
  if (selectedModel.value?.model_scope !== 'single_column') return []
  const axis = props.options?.axes.find((item) => item.key === 'column')
  if (!axis) return []
  return (selectedModel.value.allowed_columns ?? []).filter((column) => axis.values.some((value) => String(value) === column))
})
const modifierRawTotal = computed(() => modifierDrafts.value.reduce((sum, modifier) => sum + (modifier.fraction ?? 0), 0))
const normalizedModifierFractions = computed(() => {
  const sum = modifierRawTotal.value
  return sum > 0 ? modifierDrafts.value.map((modifier) => (modifier.fraction ?? 0) / sum) : []
})
const normalizedPreviewText = computed(() => normalizedModifierFractions.value.map((value) => String(value)).join(' + '))
function normalizedFractionAt(index: number): string {
  const value = normalizedModifierFractions.value[index]
  return value === undefined ? '无法归一化' : String(value)
}

function selectColumn(columnId: string) {
  selectedGroupColumnId.value = columnId
  const columnAxis = props.options?.axes.find((axis) => axis.key === 'column')
  const index = columnAxis?.values.findIndex((value) => String(value) === columnId) ?? -1
  selectedAxisIndexes.column = index >= 0 ? [String(index)] : []
}

function addAxisValue(key: SfcCandidateKey) {
  if (key === 'column' && selectedModel.value?.model_scope === 'single_column') return
  const index = pendingAxisIndex[key]
  const axis = props.options?.axes.find((item) => item.key === key)
  if (!axis || !index || axis.values[Number(index)] === undefined || selectedAxisIndexes[key].includes(index)) return
  selectedAxisIndexes[key] = [...selectedAxisIndexes[key], index].sort((a, b) => Number(a) - Number(b))
  pendingAxisIndex[key] = ''
}

function removeAxisValue(key: SfcCandidateKey, index: string) {
  selectedAxisIndexes[key] = selectedAxisIndexes[key].filter((value) => value !== index)
}

function selectedAxisValues(): Record<SfcCandidateKey, (string | number)[]> | null {
  if (!props.options || !validAxes(props.options.axes)) return null
  const axes = new Map(props.options.axes.map((axis) => [axis.key, axis]))
  const values = {} as Record<SfcCandidateKey, (string | number)[]>
  for (const key of SFC_CANDIDATE_KEYS) {
    const axis = axes.get(key)
    const indexes = selectedAxisIndexes[key]
    if (!axis || !indexes.length) return null
    const parsed = indexes.map((index) => Number(index))
    if (parsed.some((index) => !Number.isInteger(index) || index < 0 || index >= axis.values.length)) return null
    values[key] = parsed.map((index) => axis.values[index]!)
  }
  if (selectedModel.value?.model_scope === 'single_column'
    && (values.column.length !== 1 || String(values.column[0]) !== selectedGroupColumnId.value
      || !allowed_columns.value.includes(selectedGroupColumnId.value))) return null
  return values
}

function selectedAxisDraftValues(): Partial<Record<SfcCandidateKey, (string | number)[]>> {
  const axes = new Map((props.options?.axes ?? []).map((axis) => [axis.key, axis]))
  const values: Partial<Record<SfcCandidateKey, (string | number)[]>> = {}
  for (const key of SFC_CANDIDATE_KEYS) {
    const axis = axes.get(key)
    if (!axis) continue
    const indexes = selectedAxisIndexes[key].map(Number)
    if (indexes.length && indexes.every((index) => Number.isInteger(index) && index >= 0 && index < axis.values.length)) {
      values[key] = indexes.map((index) => axis.values[index]!)
    }
  }
  return values
}

watch(() => JSON.stringify([selectedAxisIndexes, selectedGroupColumnId.value]), () => emit('axes-change'))
watch(() => JSON.stringify([props.optionsState, props.options, props.initialDraft, bindingMatches.value]), () => {
  const draft = props.initialDraft
  const options = props.options
  if (restoreApplied || !draft || props.optionsState !== 'ready' || !options || !bindingMatches.value) return
  const axisMap = new Map(options.axes.map((axis) => [axis.key, axis]))
  for (const key of SFC_CANDIDATE_KEYS) {
    const axis = axisMap.get(key)
    selectedAxisIndexes[key] = axis ? (draft.axes[key] ?? []).flatMap((value) => {
      const index = axis.values.findIndex((candidate) => Object.is(candidate, value) || String(candidate) === String(value))
      return index >= 0 ? [String(index)] : []
    }) : []
  }
  const column = axisMap.get('column')
  selectedGroupColumnId.value = column?.values.some((value) => String(value) === draft.groupColumnId) ? draft.groupColumnId : ''
  expansionMode.value = options.supportedExpansionModes?.includes(draft.expansionMode) ? draft.expansionMode : 'observed_distinct_rows'
  topK.value = Number.isInteger(draft.topK) ? draft.topK : null
  totalFraction.value = typeof draft.totalFraction === 'number' && Number.isFinite(draft.totalFraction) ? draft.totalFraction : null
  normalizeFractions.value = draft.normalizeFractions === true
  modifierDrafts.value = (draft.modifiers ?? []).slice(0, options.limits.modifierMaximum).map((modifier) => ({
    key: ++modifierKey, structure: null, rawSmiles: typeof modifier.smiles === 'string' ? modifier.smiles : '',
    fraction: typeof modifier.fraction === 'number' && Number.isFinite(modifier.fraction) ? modifier.fraction : null,
  }))
  restoreApplied = true
}, { immediate: true })
watch([selectedAxisIndexes, selectedGroupColumnId, expansionMode, topK, totalFraction, normalizeFractions, modifierDrafts], () => {
  if (!bindingMatches.value) return
  // Once the user starts editing, parent draft echoes must not be treated as a new restore.
  restoreApplied = true
  emit('draft-change', {
    axes: selectedAxisDraftValues(), groupColumnId: selectedGroupColumnId.value,
    expansionMode: expansionMode.value, topK: topK.value, totalFraction: totalFraction.value,
    normalizeFractions: normalizeFractions.value,
    modifiers: modifierDrafts.value.map((modifier) => ({ smiles: modifier.rawSmiles, fraction: modifier.fraction })),
  })
}, { deep: true })
watch(supportsCoupledExpansion, (supported) => {
  if (!supported) resetExpansionMode()
})
function changeExpansionMode(event: Event) {
  const next = (event.target as HTMLSelectElement).value as SfcSearchExpansionMode
  if (next === 'coupled_discrete_conditions' && !supportsCoupledExpansion.value) return
  if (next !== 'observed_distinct_rows' && next !== 'coupled_discrete_conditions') return
  if (expansionMode.value === next) return
  expansionMode.value = next
  emit('axes-change')
}
function previewCandidates() {
  const axes = selectedAxisValues()
  if (axes) emit('preview', axes, expansionMode.value)
}

function modifierValues(): { structureRef: string; fraction: number }[] | null {
  const limits = props.options?.limits
  if (!limits || modifierDrafts.value.length < limits.modifierMinimum || modifierDrafts.value.length > limits.modifierMaximum) return null
  if (modifierDrafts.value.some((item) => (!item.rawSmiles.trim() && !item.structure)
    || (item.structure && (item.structure.project_id !== props.projectId || !item.structure.id))
    || typeof item.fraction !== 'number' || !Number.isFinite(item.fraction)
    || item.fraction < limits.componentFractionMinimum || item.fraction > limits.componentFractionMaximum)) return null
  const ids = modifierDrafts.value.flatMap((item) => item.structure?.id ? [item.structure.id] : [])
  if (new Set(ids).size !== ids.length) return null
  let fractions = modifierDrafts.value.map((item) => item.fraction!)
  if (normalizeFractions.value) {
    if (!(modifierRawTotal.value > 0)) return null
    fractions = normalizedModifierFractions.value
    if (fractions.some((value) => value < limits.componentFractionMinimum || value > limits.componentFractionMaximum)) return null
  } else if (Math.abs(fractions.reduce((sum, value) => sum + value, 0) - 1) > 1e-6) return null
  return modifierDrafts.value.map((item, index) => ({ structureRef: item.structure?.id ?? '', fraction: fractions[index]! }))
}

const selectedAxes = computed(() => selectedAxisValues())
const validModifiers = computed(() => modifierValues())
const canAddModifier = computed(() => canEdit.value && Boolean(props.options)
  && modifierDrafts.value.length < (props.options?.limits.modifierMaximum ?? 0))
const submissionError = computed(() => {
  if (!props.projectId) return '请选择可访问项目后再绑定 SFC 模型。'
  if (!selectedTool.value) return '请选择 READY 的 SFC 搜索工具。'
  if (props.optionsState === 'error') return props.optionsError || '条件加载失败，请重试。'
  if (props.optionsState === 'loading') return '正在读取并加载该模型的候选条件。'
  if (!selectedModel.value) return selectableModelChoices.value.length
    ? '请选择精确模型。'
    : '当前筛选条件下没有可用的 READY 模型。'
  if (!selectionAcknowledged.value) return '已选模型不再符合当前筛选条件，请重新选择。'
  if (!bindingMatches.value) return 'SFC 模型绑定与当前项目、工具或服务端选项不匹配；提交已停用。'
  if (!props.structuresReady) return '请填写两个主体结构；点击搜索后会自动进行服务端校验。'
  if (selectedModel.value?.model_scope === 'single_column' && !allowed_columns.value.includes(selectedGroupColumnId.value)) return '请选择该模型组允许的具体色谱柱。'
  if (!selectedAxes.value) return '请为服务端声明的八个候选轴各选择至少一个值。'
  if (!validModifiers.value) return 'Modifier 结构、组分比例或总体积分数不符合当前服务端约束。'
  if (typeof totalFraction.value !== 'number' || !Number.isFinite(totalFraction.value)
    || totalFraction.value < props.options!.limits.totalFractionMinimum || totalFraction.value > props.options!.limits.totalFractionMaximum) return 'Modifier 总体积分数不符合当前服务端约束。'
  if (typeof topK.value !== 'number' || !Number.isInteger(topK.value)
    || topK.value < props.options!.limits.topKMinimum || topK.value > props.options!.limits.topKMaximum) return 'Top-k 超出当前 schema 声明的范围。'
  return null
})
const canSubmit = computed(() => !props.disabled && !props.submitting && !props.submissionLocked && !props.candidatePreviewBlocked && !props.previewBusy && !submissionError.value)

function changeTool(event: Event) {
  const next = (event.target as HTMLSelectElement).value
  const tool = props.tools.find((item) => item.id === next)
  if (!tool || tool.health !== 'READY' || tool.newRequestReady === false) return
  toolId.value = next
  modelKey.value = ''
  thresholdFilter.value = ''
  groupFilter.value = ''
  clearSearchDraft()
  resetExpansionMode()
  emit('model-change', { modelId: null, modelVersion: null })
  emit('tool-change', next)
}

function changeThresholdFilter(event: Event) {
  const next = (event.target as HTMLSelectElement).value
  if (next && !thresholdChoices.value.includes(next)) return
  thresholdFilter.value = next
  clearSearchDraft()
  selectUniqueReadyModel()
}

function changeGroupFilter(event: Event) {
  const next = (event.target as HTMLSelectElement).value
  if (next && !groupChoices.value.some((group) => group.id === next)) return
  groupFilter.value = next
  clearSearchDraft()
  selectUniqueReadyModel()
}

function retryOptions() {
  const model = selectedModel.value
  if (model && isSelectableModel(model)) emit('model-change', { modelId: model.id, modelVersion: model.version })
}

function changeModel(event: Event) {
  const next = modelChoiceOptions.value.find((model) => modelIdentityKey(model) === (event.target as HTMLSelectElement).value)
  if (!next || !isSelectableModel(next)) return
  modelKey.value = modelIdentityKey(next)
  clearSearchDraft()
  resetExpansionMode()
  emit('model-change', { modelId: next.id, modelVersion: next.version })
}

function addModifier() {
  if (!canAddModifier.value) return
  modifierDrafts.value = [...modifierDrafts.value, { key: ++modifierKey, structure: null, rawSmiles: '', fraction: null }]
}

function removeModifier(key: number) {
  modifierDrafts.value = modifierDrafts.value.filter((item) => item.key !== key)
}

function setModifierStructure(key: number, structure: Structure) {
  if (!canEdit.value || structure.project_id !== props.projectId || !structure.id) return
  const draft = modifierDrafts.value.find((item) => item.key === key)
  if (draft) draft.structure = structure
}

function invalidateModifierStructure(key: number) {
  const draft = modifierDrafts.value.find((item) => item.key === key)
  if (draft) draft.structure = null
}

function updateModifierSmiles(key: number, rawSmiles: string) {
  const draft = modifierDrafts.value.find((item) => item.key === key)
  if (draft) draft.rawSmiles = rawSmiles
}

function submit() {
  if (!canSubmit.value || !selectedTool.value || !selectedModel.value || !props.options || !selectedAxes.value || !validModifiers.value || typeof totalFraction.value !== 'number' || typeof topK.value !== 'number') return
  const model = selectedModel.value
  emit('submit', {
    projectId: props.projectId,
    toolId: selectedTool.value.id,
    modelId: model.id,
    modelVersion: model.version,
    threshold: model.threshold!,
    model_scope: model.model_scope!,
    business_scope_id: model.business_scope_id,
    ...(model.model_scope === 'single_column' ? { group_id: model.group_id!, column_id: selectedGroupColumnId.value } : {}),
    candidateAxes: selectedAxes.value,
    expansionMode: expansionMode.value,
    modifiers: validModifiers.value,
    modifierTotalFraction: totalFraction.value,
    topK: topK.value,
    fractionsWereNormalized: normalizeFractions.value,
  })
}
</script>

<template>
  <form class="panel sfc-search-form" data-testid="sfc-search-form" @submit.prevent="submit">
    <header class="section-heading"><div><span class="eyebrow">SFC · SEARCH</span><h2>候选条件搜索</h2></div><p>条件、模型分组和候选范围均来自当前服务端能力。</p></header>
    <div class="search-grid">
      <label>预测工具<select :value="toolId" data-testid="sfc-tool-select" :disabled="formControlsDisabled" @change="changeTool"><option value="" disabled>选择服务端工具</option><option v-for="tool in tools" :key="tool.id" :value="tool.id" :disabled="tool.health !== 'READY' || tool.newRequestReady === false">{{ tool.label }} · {{ tool.health }}<template v-if="tool.schemaVersion"> · schema {{ tool.schemaVersion }}</template><template v-if="tool.unavailableReason"> · {{ tool.unavailableReason }}</template></option></select></label>
      <label>Rs 分离度目标阈值筛选<select v-model="thresholdFilter" data-testid="sfc-threshold-filter" :disabled="formControlsDisabled || !compatibleModels.length" @change="changeThresholdFilter"><option value="">全部阈值</option><option v-for="threshold in thresholdChoices" :key="threshold" :value="threshold">Rs ≥ {{ threshold }}</option></select></label>
      <label>模型组筛选<select v-model="groupFilter" data-testid="sfc-group-filter" :disabled="formControlsDisabled || !groupChoices.length" @change="changeGroupFilter"><option value="">全部模型组</option><option v-for="group in groupChoices" :key="group.id" :value="group.id">{{ group.label }} · {{ group.id }}</option></select></label>
      <label>精确模型<select :value="modelKey" data-testid="sfc-model-select" :disabled="formControlsDisabled || !selectedTool || selectedTool.health !== 'READY' || selectedTool.newRequestReady === false" @change="changeModel"><option value="" disabled>选择当前工具范围的模型</option><option v-for="model in modelChoiceOptions" :key="modelIdentityKey(model)" :value="modelIdentityKey(model)" :disabled="!isSelectableModel(model)">{{ model.label }} · {{ model.version }} · {{ model.health }}<template v-if="model.inputSchemaVersion"> · input schema {{ model.inputSchemaVersion }}</template><template v-if="model.unavailableReason"> · {{ model.unavailableReason }}</template></option></select></label>
    </div>
    <p v-if="!bindingMatches" :class="bindingError ? 'error-text' : 'hint'" role="status" data-testid="sfc-binding-status">{{ submissionError }}</p>
    <p v-if="selectedModel && selectedModel.threshold !== null" class="threshold-note" data-testid="sfc-rs-threshold">Rs 分离度目标阈值：{{ selectedModel.threshold }}；模型预测概率表示 P(Rs≥该阈值)，此阈值不是候选条件轴。</p>
    <dl v-if="selectedModel" class="group-card" data-testid="sfc-model-scope"><div><dt>科学范围</dt><dd>{{ selectedModel.model_scope === 'single_column' ? '单柱' : selectedModel.model_scope === 'all_column' ? '全柱' : '未知' }}</dd></div><div><dt>业务范围</dt><dd>{{ selectedModel.business_scope_id || '未提供' }}</dd></div><template v-if="selectedModel.model_scope === 'single_column'"><div data-testid="sfc-model-group"><dt>模型组</dt><dd>{{ selectedModel.group_label }} · {{ selectedModel.group_id }}</dd></div><div><dt>固定相 / 模式</dt><dd>{{ selectedModel.stationary_phase }} · {{ selectedModel.mode }}</dd></div></template></dl>
    <p v-else-if="selectedTool && !compatibleModels.length" class="hint" data-testid="sfc-no-scope-model">当前工具范围没有可展示的模型 metadata。</p>
    <p v-if="selectedModel && (selectedModel.health !== 'READY' || selectedModel.newRequestReady === false)" class="error-text" data-testid="sfc-model-unavailable">模型不可提交：{{ selectedModel.unavailableReason || selectedModel.health }}</p>
    <p v-else-if="selectedModel && !isSelectableModel(selectedModel)" class="error-text" data-testid="sfc-model-metadata-incomplete">模型 metadata 不完整，不能建立精确搜索绑定。</p>
    <p v-if="optionsState === 'loading'" class="hint" role="status">正在读取并加载与当前项目、工具和模型绑定的候选条件…</p>
    <div v-else-if="optionsState === 'error'" class="error-text" role="alert"><p>{{ optionsError || '条件加载失败，请重试。' }}</p><button type="button" class="secondary" data-testid="sfc-options-retry" :disabled="disabled || !selectedModel" @click="retryOptions">重试加载条件</button></div>
    <template v-if="bindingMatches">
      <label v-if="selectedModel?.model_scope === 'single_column' && selectedTool?.requiresColumn">具体色谱柱<select :value="selectedGroupColumnId" data-testid="sfc-column-select" :disabled="!canEdit" @change="selectColumn(($event.target as HTMLSelectElement).value)"><option value="">选择该组允许且候选轴存在的柱</option><option v-for="columnId in allowed_columns" :key="columnId" :value="columnId">{{ columnId }}</option></select></label>
      <p v-if="selectedModel?.model_scope === 'single_column' && selectedTool?.requiresColumn && !allowed_columns.includes(selectedGroupColumnId)" class="error-text">column_id 只能来自此精确模型组的 allowed_columns。</p>
      <p v-if="candidateError" class="error-text" role="alert" data-testid="sfc-candidate-error">{{ candidateError }}</p>
      <label v-if="supportsCoupledExpansion">候选扩展模式<select data-testid="sfc-expansion-mode" :value="expansionMode" :disabled="!canEdit" @change="changeExpansionMode"><option value="observed_distinct_rows">已观测的不同完整条件行</option><option value="coupled_discrete_conditions">合法柱规格下的离散条件组合</option></select></label><p v-if="expansionMode === 'coupled_discrete_conditions'" class="hint" data-testid="sfc-coupled-mode-hint">按目录中合法柱规格分组，在各组内组合所选的温度与流速离散值；不会生成不存在的柱规格组合。所有条件轴保留当前选择；observed 标记不是分子实验命中，Rs 阈值不是候选轴。</p><section class="axes-section"><div class="section-heading"><div><span class="eyebrow">SERVER OPTIONS</span><h3>候选条件轴</h3></div><span class="limit-pill">服务端候选上限 {{ options!.limits.candidateLimit }}</span></div><p class="hint">候选值来自当前绑定的服务端能力。observed 标记表示条件目录中存在记录，不代表当前分子实验命中；Rs 阈值不是候选轴。实际候选以服务端预览为准。</p><div class="axis-grid"><div v-for="axis in axisOptions" :key="axis.key" class="axis-picker"><label :for="`axis-${axis.key}`">{{ axis.label }}<span v-if="axis.unit">（{{ axis.unit }}）</span></label><div class="axis-add"><select :id="`axis-${axis.key}`" :data-testid="`axis-${axis.key}`" :disabled="!canEdit || (axis.key === 'column' && selectedModel?.model_scope === 'single_column')" v-model="pendingAxisIndex[axis.key]"><option value="">选择要添加的值</option><option v-for="(value, index) in axis.values" :key="`${typeof value}:${String(value)}:${index}`" :value="String(index)" :disabled="selectedAxisIndexes[axis.key].includes(String(index))">{{ value }}</option></select><button type="button" class="secondary" :data-testid="`axis-add-${axis.key}`" :disabled="!canEdit || !pendingAxisIndex[axis.key] || selectedAxisIndexes[axis.key].includes(pendingAxisIndex[axis.key])" @click="addAxisValue(axis.key)">添加</button></div><ul v-if="selectedAxisIndexes[axis.key].length" class="axis-chips" :data-testid="`axis-chips-${axis.key}`"><li v-for="index in selectedAxisIndexes[axis.key]" :key="index"><span>{{ axis.values[Number(index)] }}</span><button type="button" :data-testid="`axis-remove-${axis.key}-${index}`" :disabled="!canEdit" :aria-label="`移除${axis.label} ${axis.values[Number(index)]}`" @click="removeAxisValue(axis.key, index)">×</button></li></ul><small v-if="axis.key === 'column' && selectedModel?.model_scope === 'single_column'">单柱搜索的 column 轴由上方具体色谱柱选择同步。</small></div></div></section>
      <p v-if="scopeOptionsError" class="error-text" role="alert" data-testid="sfc-scoped-options-error">{{ scopeOptionsError }} <button type="button" class="secondary" data-testid="sfc-scoped-options-retry" :disabled="disabled || submitting" @click="emit('retry-scope-options')">重试读取此候选范围</button></p>
      <div class="candidate-preview" data-testid="sfc-candidate-preview"><button type="button" class="secondary" data-testid="sfc-preview-button" :disabled="!canEdit || previewBusy || !selectedAxisValues()" @click="previewCandidates">{{ previewBusy ? '正在检查候选范围…' : '检查候选范围' }}</button><p v-if="candidatePreviewText" role="status">{{ candidatePreviewText }}</p><p v-else class="hint">{{ expansionMode === 'observed_distinct_rows' ? '检查观测到的完整条件行匹配数。' : '检查各合法柱规格组内的温度与流速离散组合。' }}</p></div>
      <p v-if="predictionError" class="error-text" role="alert" data-testid="sfc-prediction-submit-error">{{ predictionError }}</p>
      <p v-if="predictionStatusError" class="error-text" role="alert" data-testid="sfc-prediction-status-error">{{ predictionStatusError }} <strong v-if="acceptedPredictionId">已接受任务 {{ acceptedPredictionId }}。</strong><button v-if="acceptedPredictionId" type="button" class="secondary" data-testid="sfc-prediction-status-retry" :disabled="disabled || submitting" @click="emit('retry-prediction-status')">重新读取此任务状态</button></p>
      <section class="modifier-section"><div class="section-heading"><div><span class="eyebrow">MODIFIER</span><h3>Modifier 组分</h3></div><button type="button" class="ghost" data-testid="add-modifier" :disabled="!canAddModifier" @click="addModifier">添加结构</button></div><p class="hint">各组分 fraction 之和须为 1；总体积分数是独立字段，不与组分比例相加。</p><div v-for="(modifier,index) in modifierDrafts" :key="modifier.key" class="modifier-row" :data-testid="`modifier-row-${modifier.key}`"><StructureInput :target-id="`modifier:${modifier.key}`" label="Modifier 组分" example-smiles="CCO" :initial-smiles="modifier.rawSmiles" :project-id="projectId" :disabled="structureInputDisabled" @normalized="setModifierStructure(modifier.key, $event)" @invalidated="invalidateModifierStructure(modifier.key)" @draft-change="updateModifierSmiles(modifier.key, $event)" /><label>组分 fraction<input v-model.number="modifier.fraction" type="number" :min="options!.limits.componentFractionMinimum" :max="options!.limits.componentFractionMaximum" step="any" :disabled="!canEdit" :data-testid="`modifier-fraction-${modifier.key}`" /><small v-if="normalizeFractions" :data-testid="`normalized-fraction-${modifier.key}`">提交归一化值：{{ normalizedFractionAt(index) }}</small></label><button type="button" class="ghost" :disabled="!canEdit" @click="removeModifier(modifier.key)">移除</button></div><label>Modifier 总体积分数<input v-model.number="totalFraction" type="number" :min="options!.limits.totalFractionMinimum" :max="options!.limits.totalFractionMaximum" step="any" data-testid="modifier-total-fraction" :disabled="!canEdit" /></label><label class="normalize-choice"><input v-model="normalizeFractions" type="checkbox" data-testid="normalize-fractions" :disabled="!canEdit" /> 显示并提交归一化后的组分 fraction</label><p v-if="normalizeFractions" class="normalization-preview" data-testid="normalization-preview">归一化预览（提交值）：{{ normalizedPreviewText || '无法归一化：组分总和须大于 0' }}</p></section>
      <label>Top-k <input v-model.number="topK" type="number" :min="options!.limits.topKMinimum" :max="options!.limits.topKMaximum" step="1" data-testid="sfc-top-k" :disabled="!canEdit" /><small>schema 范围 {{ options!.limits.topKMinimum }}–{{ options!.limits.topKMaximum }}</small></label>
    </template>
    <p v-if="submissionError" class="error-text" data-testid="sfc-search-validation">{{ submissionError }}</p>
    <button type="submit" class="primary" data-testid="sfc-search-submit" :disabled="!canSubmit">{{ submitting ? '正在提交搜索…' : '搜索 SFC 候选' }}</button>
  </form>
</template>

<style scoped>
.sfc-search-form { display: grid; gap: 15px; min-width: 0; color: #24445f; }
.section-heading { display: flex; align-items: start; justify-content: space-between; gap: 12px; }
.section-heading h2,.section-heading h3 { margin: 3px 0 0; color: #173957; }
.section-heading p,.hint { margin: 0; color: #6b8193; font-size: 13px; line-height: 1.5; }
.search-grid,.axis-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 11px; }
label { display: grid; gap: 6px; min-width: 0; color: #486277; font-size: 13px; font-weight: 600; }
select,input { box-sizing: border-box; width: 100%; min-width: 0; border: 1px solid #cbdce9; border-radius: 8px; padding: 9px; background: #fff; color: #24445f; font: inherit; }
.threshold-note,.group-card,.normalization-preview { margin: 0; border: 1px solid #d9e8f2; border-radius: 9px; padding: 10px 12px; background: #f4faff; color: #365e7b; font-size: 13px; line-height: 1.5; }
.group-card { display: grid; gap: 8px; }
.group-card div { display: grid; grid-template-columns: 130px minmax(0,1fr); gap: 10px; }
.group-card dt { color: #6b8193; }
.group-card dd { min-width: 0; margin: 0; overflow-wrap: anywhere; }
.axes-section,.modifier-section { display: grid; gap: 10px; min-width: 0; border-top: 1px solid #e4edf4; padding-top: 13px; }
.axis-picker { display: grid; gap: 6px; min-width: 0; }
.axis-add { display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 7px; }
.secondary { border: 1px solid #cbdce9; border-radius: 8px; padding: 8px 10px; background: #f4faff; color: #2f5876; font: inherit; cursor: pointer; }
.axis-chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0; padding: 0; list-style: none; }
.axis-chips li { display: inline-flex; align-items: center; gap: 5px; border: 1px solid #c8dfef; border-radius: 999px; padding: 3px 8px; background: #eef8ff; color: #2f5876; font-size: 12px; }
.axis-chips button { border: 0; padding: 0 2px; background: transparent; color: inherit; font: inherit; cursor: pointer; }
.limit-pill { border-radius: 999px; padding: 5px 9px; background: #eaf5ff; color: #1677c8; font-size: 12px; white-space: nowrap; }
.modifier-row { display: grid; grid-template-columns: minmax(0,1fr) minmax(120px,.45fr) auto; align-items: end; gap: 9px; min-width: 0; border: 1px solid #e2eaf0; border-radius: 10px; padding: 9px; background: #fbfdff; }
.normalize-choice { display: flex; align-items: center; gap: 8px; }
.normalize-choice input { width: auto; }
.primary,.ghost { border: 1px solid #d4e2ec; border-radius: 8px; padding: 9px 12px; background: #fff; color: #2f5876; font: inherit; font-weight: 600; cursor: pointer; }
.primary { border-color: #1688e8; background: linear-gradient(135deg,#1688e8,#0d6ec3); color: #fff; }
.primary:disabled,.ghost:disabled { cursor: not-allowed; opacity: .55; }
.error-text { margin: 0; color: #ad4335; font-size: 13px; line-height: 1.5; }
small { color: #6b8193; font-weight: 400; }
@media (max-width: 680px) { .search-grid,.axis-grid { grid-template-columns: 1fr; } .modifier-row { grid-template-columns: 1fr; } .group-card div { grid-template-columns: 1fr; gap: 3px; } .section-heading { flex-wrap: wrap; } }
</style>

