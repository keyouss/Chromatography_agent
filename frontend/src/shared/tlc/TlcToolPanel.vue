<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import TlcCurveExplorer from './TlcCurveExplorer.vue'
import { estimateRatio, isValidRf, type CurvePoint } from './interaction'
import { firstTlcReverseRatioStep, isTlcReverseRangeValid, isTlcReverseRatioStepAllowed, isTlcSolventId, parseTlcFraction } from './types'
import type {
  TlcBatchSubmit,
  TlcBatchSummary,
  TlcCandidateSystem,
  TlcInput,
  TlcResultState,
  TlcReverseRange,
  TlcReverseSubmit,
  TlcSolventOption,
  TlcTool,
  TlcSolventId,
} from './types'

const props = withDefaults(defineProps<{
  modelValue?: TlcTool
  initialInput?: TlcInput | null
  initialDraft?: TlcToolPanelWorkspaceDraft | null
  visibleTools?: readonly TlcTool[]
  hideNavigation?: boolean
  busy?: boolean
  structureRef?: string | null
  structureInputReady?: boolean
  solventOptions: readonly TlcSolventOption[]
  candidateSystems: readonly TlcCandidateSystem[]
  reverseRange: TlcReverseRange
  points?: readonly CurvePoint[]
  initialRf?: number
  resultState?: TlcResultState
  resultMessage?: string | null
  failedPointCount?: number
  batchArtifactId?: string | null
  batchTemplateVersion?: string | null
  batchSummary?: TlcBatchSummary | null
}>(), {
  modelValue: 'single',
  visibleTools: () => ['single', 'batch', 'condition_compare', 'reverse_solvent'],
  hideNavigation: false,
  busy: false,
  structureRef: null,
  structureInputReady: false,
  points: () => [],
  initialRf: 0.5,
  resultState: 'empty',
  resultMessage: null,
  failedPointCount: 0,
  batchArtifactId: null,
  batchTemplateVersion: null,
  batchSummary: null,
})

const emit = defineEmits<{
  'update:modelValue': [tool: TlcTool]
  'submit-single': [input: TlcInput]
  'request-batch-input': []
  'submit-batch': [draft: TlcBatchSubmit]
  'submit-comparison': [conditions: readonly TlcInput[]]
  'submit-reverse': [draft: TlcReverseSubmit]
  'rf-change': [rf: number]
  'draft-change': [draft: TlcToolPanelWorkspaceDraft]
}>()

type ComponentDraft = { solvent_id: TlcSolventId; fraction: number; ratioInput: string }
export type TlcToolPanelWorkspaceDraft = {
  single: ComponentDraft[]
  batch: ComponentDraft[]
  comparison: ComponentDraft[][]
  reverse: { min: number; max: number; ratioStep: number | null; maxCandidates: number; selectedCandidateIds: string[] }
}

const sourceNote = '交互参考来源：mo-group 在线工具（platform.js / platform/index.html）；商业授权状态：待核验。'
const tools: readonly { id: TlcTool; label: string }[] = [
  { id: 'single', label: '单次预测' },
  { id: 'batch', label: '批量预测' },
  { id: 'condition_compare', label: '条件比较' },
  { id: 'reverse_solvent', label: '反推溶剂' },
]

const activeTool = ref<TlcTool>(props.modelValue)
const visibleTools = computed(() => tools.filter((tool) => props.visibleTools.includes(tool.id)))
const singleComponents = ref<ComponentDraft[]>([])
const batchComponents = ref<ComponentDraft[]>([])
const comparisonComponents = ref<ComponentDraft[][]>([])
const targetMin = ref(props.reverseRange.target_rf.min)
const targetMax = ref(props.reverseRange.target_rf.max)
const ratioStep = ref<number | null>(firstTlcReverseRatioStep(props.reverseRange.ratio_step))
const maxCandidates = ref(props.reverseRange.max_candidates)
const selectedCandidateIds = ref<string[]>([])

function newComponents(): ComponentDraft[] {
  return props.solventOptions.slice(0, 2).flatMap((option, index) => isTlcSolventId(option.id) ? [{
    solvent_id: option.id,
    fraction: props.solventOptions.length > 1 ? 0.5 : index === 0 ? 1 : 0,
    ratioInput: props.solventOptions.length > 1 ? '0.5' : index === 0 ? '1' : '0',
  }] : [])
}

function syncComponents(target: ComponentDraft[]): void {
  const allowed = new Set(props.solventOptions.map((option) => option.id))
  for (const item of target) {
    if (!allowed.has(item.solvent_id)) {
      const first = props.solventOptions[0]
      if (first && isTlcSolventId(first.id)) item.solvent_id = first.id
    }
  }
}

watch(() => props.modelValue, (next) => { activeTool.value = next })
watch(() => props.solventOptions, () => {
  if (!singleComponents.value.length) singleComponents.value = newComponents()
  if (!batchComponents.value.length) batchComponents.value = newComponents()
  syncComponents(singleComponents.value)
  syncComponents(batchComponents.value)
  comparisonComponents.value.forEach(syncComponents)
}, { deep: true, immediate: true })
watch([() => props.initialInput, () => props.solventOptions], ([input, options]) => {
  const components = input?.solvent_components
  if (!components?.length || !options.length || components.some((item) => !options.some((option) => option.id === item.solvent_id))) return
  singleComponents.value = components.map((item) => ({ solvent_id: item.solvent_id, fraction: item.fraction, ratioInput: String(item.fraction) }))
}, { deep: true, immediate: true })
let restoringDraft = false
watch([() => props.initialDraft, () => props.solventOptions], ([draft, options]) => {
  if (!draft || !options.length) return
  const restore = (items: readonly ComponentDraft[] | undefined) => (items ?? []).filter((item) =>
    options.some((option) => option.id === item.solvent_id) && Number.isFinite(item.fraction)
    && item.fraction >= 0 && item.fraction <= 1 && typeof item.ratioInput === 'string')
    .map((item) => ({ solvent_id: item.solvent_id, fraction: item.fraction, ratioInput: item.ratioInput }))
  restoringDraft = true
  singleComponents.value = restore(draft.single)
  batchComponents.value = restore(draft.batch)
  comparisonComponents.value = (draft.comparison ?? []).slice(0, 200).map((items) => restore(items))
  const reverse = draft.reverse
  if (reverse && Number.isFinite(reverse.min) && Number.isFinite(reverse.max)
    && Number.isFinite(reverse.maxCandidates) && (reverse.ratioStep === null || Number.isFinite(reverse.ratioStep))) {
    targetMin.value = reverse.min; targetMax.value = reverse.max; ratioStep.value = reverse.ratioStep
    maxCandidates.value = reverse.maxCandidates
    selectedCandidateIds.value = Array.isArray(reverse.selectedCandidateIds)
      ? reverse.selectedCandidateIds.filter((id): id is string => typeof id === 'string') : []
  }
  queueMicrotask(() => { restoringDraft = false })
}, { deep: true, immediate: true })
watch([singleComponents, batchComponents, comparisonComponents, targetMin, targetMax, ratioStep, maxCandidates, selectedCandidateIds], () => {
  if (restoringDraft || !props.solventOptions.length) return
  emit('draft-change', {
    single: singleComponents.value.map((item) => ({ ...item })),
    batch: batchComponents.value.map((item) => ({ ...item })),
    comparison: comparisonComponents.value.map((items) => items.map((item) => ({ ...item }))),
    reverse: { min: targetMin.value, max: targetMax.value, ratioStep: ratioStep.value, maxCandidates: maxCandidates.value, selectedCandidateIds: [...selectedCandidateIds.value] },
  })
}, { deep: true })
watch(() => props.reverseRange, (next) => {
  targetMin.value = next.target_rf.min
  targetMax.value = next.target_rf.max
  ratioStep.value = firstTlcReverseRatioStep(next.ratio_step)
  maxCandidates.value = next.max_candidates
}, { deep: true })

function selectTool(tool: TlcTool): void {
  activeTool.value = tool
  emit('update:modelValue', tool)
}

function addComponent(target: ComponentDraft[]): void {
  const option = props.solventOptions.find((item) => !target.some((component) => component.solvent_id === item.id))
  if (option && target.length < 5 && isTlcSolventId(option.id)) target.push({ solvent_id: option.id, fraction: 0, ratioInput: '0' })
}

function removeComponent(target: ComponentDraft[], index: number): void {
  if (target.length > 1) target.splice(index, 1)
}

function validateComponents(target: readonly ComponentDraft[]): string | null {
  if (target.length < 2 || target.length > 5) return '模型要求 2–5 个不重复的溶剂组分。'
  if (target.some((item) => !item.solvent_id || !Number.isFinite(item.fraction) || item.fraction < 0 || item.fraction > 1)) {
    return '每个溶剂比例必须在 0–1 之间。'
  }
  if (new Set(target.map((item) => item.solvent_id)).size !== target.length) return '溶剂不能重复。'
  const sum = target.reduce((total, item) => total + item.fraction, 0)
  if (Math.abs(sum - 1) > 1e-6) return '溶剂比例之和必须为 1。'
  return null
}

function toInput(target: readonly ComponentDraft[]): TlcInput {
  return { solvent_components: target.map(({ solvent_id, fraction }) => ({ solvent_id, fraction })) }
}

function updateFraction(component: ComponentDraft, event: Event): void {
  const value = (event.target as HTMLInputElement).value
  component.ratioInput = value
  component.fraction = parseTlcFraction(value)
}

const singleError = computed(() => validateComponents(singleComponents.value))
const batchError = computed(() => validateComponents(batchComponents.value))
const comparisonErrors = computed(() => comparisonComponents.value.map(validateComponents))
const comparisonReady = computed(() => !props.busy && comparisonComponents.value.length > 0 && comparisonErrors.value.every((error) => !error))
const hasStructure = computed(() => Boolean(props.structureRef?.trim()) || props.structureInputReady)
const singleReady = computed(() => !props.busy && hasStructure.value && !singleError.value)
const batchReady = computed(() => !props.busy && Boolean(props.batchArtifactId) && !batchError.value)

function submitSingle(): void {
  if (singleReady.value) emit('submit-single', toInput(singleComponents.value))
}

function submitBatch(): void {
  if (!batchReady.value || !props.batchArtifactId) return
  emit('submit-batch', { input_artifact_id: props.batchArtifactId, common_input: toInput(batchComponents.value) })
}

function addComparison(): void {
  if (comparisonComponents.value.length >= 200) return
  comparisonComponents.value.push(newComponents())
}

function removeComparison(index: number): void {
  if (comparisonComponents.value.length > 1) comparisonComponents.value.splice(index, 1)
}

function submitComparison(): void {
  if (hasStructure.value && comparisonReady.value) emit('submit-comparison', comparisonComponents.value.map(toInput))
}

const validPointCount = computed(() => props.points.filter((point) => isValidRf(point.rf)).length)
const inferredPointState = computed(() => {
  if (!props.points.length || !validPointCount.value) return 'empty'
  if (props.points.length !== validPointCount.value) return 'partial_failure'
  return 'success'
})
const estimatedReverse = computed(() => estimateRatio(targetMin.value, props.points))
const reverseState = computed(() => {
  if (props.resultState !== 'empty') return props.resultState
  if (inferredPointState.value === 'partial_failure') return 'partial_failure'
  if (inferredPointState.value === 'empty') return 'empty'
  return estimatedReverse.value.status === 'unreachable' ? 'unreachable' : 'success'
})
const reverseStateLabel = computed(() => ({
  empty: '等待曲线点',
  loading: '计算中',
  success: '可用',
  unreachable: '目标 Rf 不可达',
  partial_failure: '部分扫描失败',
  failed: '计算失败',
} satisfies Record<TlcResultState, string>)[reverseState.value])

const reverseCapabilityError = computed(() => {
  if (props.reverseRange.available === false) return props.reverseRange.unavailable_reason || '服务端未返回 TLC 反推能力。'
  if (!isTlcReverseRangeValid(props.reverseRange)) return '服务端未返回完整的 TLC 反推范围能力。'
  if (!props.candidateSystems.length) return '服务端未返回可用的 TLC 候选体系。'
  return null
})
const reverseReady = computed(() => {
  if (props.busy || reverseCapabilityError.value || !hasStructure.value || !selectedCandidateIds.value.length) return false
  const bounds = props.reverseRange.target_rf
  const validTarget = Number.isFinite(targetMin.value) && Number.isFinite(targetMax.value) &&
    targetMin.value >= bounds.min && targetMax.value <= bounds.max && targetMin.value <= targetMax.value
  const validStep = isTlcReverseRatioStepAllowed(ratioStep.value, props.reverseRange.ratio_step)
  const validMax = Number.isInteger(maxCandidates.value) && maxCandidates.value > 0 && maxCandidates.value <= props.reverseRange.max_candidates
  return validTarget && validStep && validMax
})

function submitReverse(): void {
  const bounds = props.reverseRange.target_rf
  const validTarget = Number.isFinite(targetMin.value) && Number.isFinite(targetMax.value) &&
    targetMin.value >= bounds.min && targetMax.value <= bounds.max && targetMin.value <= targetMax.value
  const validStep = isTlcReverseRatioStepAllowed(ratioStep.value, props.reverseRange.ratio_step)
  const validMax = Number.isInteger(maxCandidates.value) && maxCandidates.value > 0 && maxCandidates.value <= props.reverseRange.max_candidates
  if (!reverseReady.value || !validTarget || !validStep || !validMax || ratioStep.value === null) return
  emit('submit-reverse', {
    target_rf: { min: targetMin.value, max: targetMax.value },
    candidate_system_ids: [...selectedCandidateIds.value],
    ratio_step: ratioStep.value,
    max_candidates: maxCandidates.value,
  })
}
</script>

<template>
  <section class="tlc-tools" aria-label="TLC 专用工具面板">
    <nav v-if="!hideNavigation" class="tool-tabs" aria-label="TLC 工具">
      <button v-for="tool in visibleTools" :key="tool.id" type="button" :class="{ active: activeTool === tool.id }" @click="selectTool(tool.id)">
        {{ tool.label }}
      </button>
    </nav>

    <div class="context-row">
      <span class="context-label">分子输入</span>
      <span v-if="structureRef?.trim()" class="context-value">分子已就绪</span>
      <span v-else-if="structureInputReady" class="context-value muted">点击预测时自动校验</span>
      <span v-else class="context-value muted">请先在结构入口输入分子</span>
    </div>

    <div v-if="activeTool === 'single'" class="input-layout">
      <section class="card">
        <div class="card-heading"><div><h2>单次 TLC 预测</h2><p>使用模型声明的命名溶剂组成。</p></div><span class="badge">TLC</span></div>
        <div class="component-list">
          <div v-for="(component, index) in singleComponents" :key="index" class="component-row">
            <label>溶剂 {{ index + 1 }}<select v-model="component.solvent_id"><option v-for="option in solventOptions" :key="option.id" :value="option.id">{{ option.label || option.id }}</option></select></label>
            <label>比例<input :value="component.ratioInput" type="text" inputmode="decimal" placeholder="0.8 或 8:2" @input="updateFraction(component, $event)"><small>归一化后 {{ Number.isFinite(component.fraction) ? component.fraction.toFixed(2) : '无效' }}</small></label>
            <button type="button" class="icon-button" :disabled="singleComponents.length <= 1" aria-label="删除溶剂" @click="removeComponent(singleComponents, index)">删除</button>
          </div>
        </div>
        <button type="button" class="secondary" :disabled="singleComponents.length >= 5 || singleComponents.length >= solventOptions.length" @click="addComponent(singleComponents)">添加溶剂</button>
        <p v-if="singleError" class="field-error">{{ singleError }}</p>
        <p class="help">比例采用 0–1 小数，组件不会自动归一化。</p>
        <button type="button" class="primary" :disabled="!singleReady" @click="submitSingle">提交单次预测</button>
      </section>
    </div>

    <div v-else-if="activeTool === 'batch'" class="tool-grid">
      <section class="card">
        <div class="card-heading"><div><h2>批量 TLC 预测</h2><p>文件先由制品服务校验，组件只接收已准备的制品引用。</p></div><span class="badge">批量</span></div>
        <div class="artifact-box" :class="{ ready: batchArtifactId }">
          <strong>{{ batchArtifactId ? '批量输入制品已就绪' : '尚未选择批量输入制品' }}</strong>
          <span v-if="batchTemplateVersion">模板版本：{{ batchTemplateVersion }}</span>
          <button type="button" class="secondary" @click="emit('request-batch-input')">选择或校验批量文件</button>
        </div>
        <div class="component-list">
          <div v-for="(component, index) in batchComponents" :key="index" class="component-row">
            <label>统一溶剂 {{ index + 1 }}<select v-model="component.solvent_id"><option v-for="option in solventOptions" :key="option.id" :value="option.id">{{ option.label || option.id }}</option></select></label>
            <label>比例<input :value="component.ratioInput" type="text" inputmode="decimal" @input="updateFraction(component, $event)"></label>
            <button type="button" class="icon-button" :disabled="batchComponents.length <= 1" aria-label="删除统一溶剂" @click="removeComponent(batchComponents, index)">删除</button>
          </div>
        </div>
        <button type="button" class="secondary" :disabled="batchComponents.length >= 5 || batchComponents.length >= solventOptions.length" @click="addComponent(batchComponents)">添加统一溶剂</button>
        <p v-if="batchError" class="field-error">{{ batchError }}</p><button type="button" class="primary" :disabled="!batchReady" @click="submitBatch">提交批量预测</button>
      </section>
      <section class="card result-card">
        <h2>批量校验与进度</h2>
        <p v-if="!batchSummary" class="empty-copy">上传并校验后显示逐行状态。</p>
        <dl v-else class="summary-grid"><div><dt>总行数</dt><dd>{{ batchSummary.total }}</dd></div><div><dt>有效</dt><dd>{{ batchSummary.valid }}</dd></div><div><dt>无效</dt><dd>{{ batchSummary.invalid }}</dd></div><div><dt>失败</dt><dd>{{ batchSummary.failed ?? 0 }}</dd></div></dl>
        <p v-if="resultState === 'partial_failure'" class="warning">部分失败：成功结果保留，失败行可由父层安排重试。</p>
      </section>
    </div>

    <div v-else-if="activeTool === 'condition_compare'" class="input-layout">
      <section class="card">
        <div class="card-heading"><div><h2>TLC 条件比较</h2><p>同一结构下提交多组独立 TLC 条件。</p></div><span class="badge">比较</span></div>
        <div v-if="!comparisonComponents.length" class="empty-inline"><p>还没有比较条件。</p><button type="button" class="secondary" @click="addComparison">添加条件</button></div>
        <div v-for="(condition, conditionIndex) in comparisonComponents" :key="conditionIndex" class="condition-block">
          <div class="condition-title"><strong>条件 {{ conditionIndex + 1 }}</strong><button type="button" class="icon-button" :disabled="comparisonComponents.length <= 1" @click="removeComparison(conditionIndex)">删除</button></div>
          <div v-for="(component, index) in condition" :key="index" class="component-row">
            <label>溶剂 {{ index + 1 }}<select v-model="component.solvent_id"><option v-for="option in solventOptions" :key="option.id" :value="option.id">{{ option.label || option.id }}</option></select></label>
            <label>比例<input :value="component.ratioInput" type="text" inputmode="decimal" placeholder="0.8 或 8:2" @input="updateFraction(component, $event)"><small>归一化后 {{ Number.isFinite(component.fraction) ? component.fraction.toFixed(2) : '无效' }}</small></label>
            <button type="button" class="icon-button" :disabled="condition.length <= 1" @click="removeComponent(condition, index)">删除</button>
          </div>
          <button type="button" class="text-button" :disabled="condition.length >= 5 || condition.length >= solventOptions.length" @click="addComponent(condition)">+ 添加溶剂</button>
          <p v-if="comparisonErrors[conditionIndex]" class="field-error">{{ comparisonErrors[conditionIndex] }}</p>
        </div>
        <button type="button" class="secondary" :disabled="comparisonComponents.length >= 200" @click="addComparison">添加比较条件</button>
        <button type="button" class="primary" :disabled="!hasStructure || !comparisonReady" @click="submitComparison">提交条件比较</button>
      </section>
    </div>

    <div v-else class="reverse-layout">
      <section class="card">
        <div class="card-heading"><div><h2>TLC 反推溶剂</h2><p>候选体系和扫描范围均来自能力注入。</p></div><span class="badge">反推</span></div>
        <fieldset><legend>目标 Rf 范围</legend><div class="range-row"><label>下限<input v-model.number="targetMin" type="number" :min="reverseRange.target_rf.min" :max="reverseRange.target_rf.max" step="0.01"></label><label>上限<input v-model.number="targetMax" type="number" :min="reverseRange.target_rf.min" :max="reverseRange.target_rf.max" step="0.01"></label></div></fieldset>
        <fieldset><legend>候选体系</legend><p v-if="!candidateSystems.length" class="empty-copy">当前构建没有可用候选体系。</p><label v-for="candidate in candidateSystems" :key="candidate.id" class="check-row"><input v-model="selectedCandidateIds" type="checkbox" :value="candidate.id">{{ candidate.label || candidate.id }}</label></fieldset>
        <label>扫描步长<input v-model.number="ratioStep" type="number" :min="reverseRange.ratio_step.min" :max="reverseRange.ratio_step.max" step="any" :disabled="Boolean(reverseCapabilityError)"></label>
        <label>最多候选数<input v-model.number="maxCandidates" type="number" min="1" :max="reverseRange.max_candidates" step="1"></label>
        <p v-if="reverseCapabilityError" class="field-error">{{ reverseCapabilityError }}</p>
        <button type="button" class="primary" :disabled="!reverseReady || busy" @click="submitReverse">{{ busy ? '正在提交…' : '提交正式反推' }}</button>
        <p class="help">正式反推由服务端计算；下方曲线拖动只用于交互估计。</p>
      </section>
      <section class="card curve-card">
        <div class="result-heading"><h2>曲线与交互估计</h2><span class="status" :data-state="reverseState">{{ reverseStateLabel }}</span></div>
        <TlcCurveExplorer :points="points" solvent-a="候选体系" solvent-b="比例" :initial-rf="initialRf" @change="emit('rf-change', $event)" />
        <p v-if="reverseState === 'empty'" class="empty-copy">暂无有效曲线点；空点不会被插值。</p>
        <p v-else-if="reverseState === 'unreachable'" class="warning">目标 Rf 不在当前有效曲线可达范围内，请调整目标或候选体系。</p>
        <p v-else-if="reverseState === 'partial_failure'" class="warning">{{ failedPointCount || points.length - validPointCount }} 个扫描点失败，失败点之间不会连线或插值。</p>
        <p v-if="resultState === 'success'" class="help">服务端正式结果与前端交互估计分开显示。</p>
      </section>
    </div>

    <footer class="source-note">{{ sourceNote }}</footer>
  </section>
</template>

<style scoped>
.tlc-tools{display:grid;gap:14px;color:#19352e}.tool-tabs{display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid #d7e1dd;padding-bottom:10px}.tool-tabs button,.secondary,.primary,.icon-button,.text-button{border:1px solid #c8d7d1;border-radius:8px;background:#fff;color:#245246;cursor:pointer;font:inherit}.tool-tabs button{padding:8px 13px}.tool-tabs button.active{background:#0f6e56;color:#fff;border-color:#0f6e56}.context-row{display:flex;gap:10px;align-items:center;font-size:13px}.context-label{color:#657871}.context-value{font-family:ui-monospace,monospace}.muted,.help,.empty-copy{color:#657871}.tool-grid,.reverse-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:14px}.input-layout{display:grid;grid-template-columns:minmax(0,1fr);gap:14px}.card{border:1px solid #d7e1dd;border-radius:14px;background:#fff;padding:18px;min-width:0}.card-heading,.result-heading,.condition-title{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.card h2,.result-heading h2{margin:0;font-size:17px}.card-heading p{margin:5px 0 0;color:#657871;font-size:13px}.badge,.status{border-radius:999px;background:#eaf4f0;color:#0f6e56;padding:4px 9px;font-size:12px;white-space:nowrap}.status[data-state=unreachable],.status[data-state=partial_failure],.warning{color:#9a5a20}.status[data-state=unreachable],.status[data-state=partial_failure]{background:#fff3e5}.component-list{display:grid;gap:8px;margin:18px 0 12px}.component-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,.75fr) auto;grid-template-rows:auto minmax(36px,auto) auto;gap:5px 8px;align-items:start}.component-row label,.card>label,fieldset label{display:grid;gap:5px;min-width:0;font-size:12px;color:#657871}.component-row select,.component-row input,.card>label select,.card>label input,fieldset input,fieldset select{box-sizing:border-box;width:100%;min-width:0;min-height:36px;border:1px solid #c8d7d1;border-radius:7px;padding:6px 8px;background:#fff;color:#19352e;font:inherit}.component-row>label{grid-row:1/span 3;grid-template-rows:subgrid}.component-row>label>select,.component-row>label>input{grid-row:2;align-self:stretch}.component-row>label>small{grid-row:3}.component-row>.icon-button{grid-row:2;align-self:stretch}.icon-button{padding:8px 10px;font-size:12px}.icon-button:disabled,.secondary:disabled,.primary:disabled,.text-button:disabled{opacity:.45;cursor:not-allowed}.secondary,.primary{padding:8px 12px;margin-top:8px}.primary{display:block;width:100%;margin-top:16px;background:#0f6e56;border-color:#0f6e56;color:#fff}.text-button{border:0;padding:3px 0;margin-top:7px;background:transparent}.field-error{margin:8px 0;color:#b04738;font-size:12px}.help,.source-note{font-size:12px;line-height:1.5}.result-card{min-height:190px}.empty-copy{font-size:13px;line-height:1.5}.artifact-box{display:grid;gap:7px;padding:14px;margin:18px 0;border:1px dashed #c8d7d1;border-radius:10px;background:#f8fbfa}.artifact-box.ready{border-style:solid;background:#f0f8f4}.artifact-box span{font-size:12px;color:#657871}.artifact-box button{justify-self:start}.summary-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:18px 0}.summary-grid div{border:1px solid #e1ebe7;border-radius:8px;padding:10px}.summary-grid dt{font-size:11px;color:#657871}.summary-grid dd{margin:4px 0 0;font-weight:700}.condition-block{border-top:1px solid #e7efec;margin-top:14px;padding-top:14px}.condition-title{align-items:center;margin-bottom:8px}.empty-inline{padding:16px;border:1px dashed #c8d7d1;border-radius:9px}.empty-inline p{margin:0 0 6px}.range-row{display:grid;grid-template-columns:1fr 1fr;gap:8px}.card fieldset{border:1px solid #e1ebe7;border-radius:9px;margin:16px 0;padding:10px}.card legend{padding:0 5px;color:#657871;font-size:12px}.check-row{display:flex;grid-template-columns:none;gap:7px;align-items:center;margin:7px 0;color:#19352e!important}.curve-card{overflow:hidden}.curve-card :deep(.tlc-explorer){margin-top:14px}.source-note{border-top:1px solid #e7efec;padding-top:10px;color:#657871}@media(max-width:760px){.tool-grid,.reverse-layout{grid-template-columns:1fr}.component-row{grid-template-columns:minmax(0,1fr) minmax(0,.8fr) auto}.summary-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:520px){.component-row{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.component-row .icon-button{grid-column:1/-1;grid-row:4;justify-self:end}}
</style>
