<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { activeProfile } from '@/app/generated/profile'
import StructureInput from '@/components/StructureInput.vue'
import StructureWorkspace from '@/components/StructureWorkspace.vue'
import SharedKetcherCanvas from '@/components/SharedKetcherCanvas.vue'
import type { ContractPrediction, ContractTlcReverseBody } from '@/core/contract-types'
import type { Structure } from '@/core/types'
import { projectSelectionState, resolveProjectId } from '@/core/project-context'
import { createStructureWorkspaceContext, type StructureWorkspaceContext } from '@/core/structure/workspace'
import { statusLabel } from '@/core/status-labels'
import { useAppStore } from '@/stores/app'
import { usePredictionStore } from '@/shared/prediction/store'
import { emptyPredictionWorkspace, predictionWorkspaceKey, readPredictionWorkspace, writePredictionWorkspace, type PredictionWorkspace } from '@/shared/prediction/workspace'
import { isPredictionTerminalStatus, mapTlcReverseCurve, resultMetadataError, type PredictionResultPayload } from '@/shared/prediction/result'
import { formatResultCell } from '@/shared/prediction/result'
import { ApiClientError } from '@/core/api/client'
import TlcToolPanel from '@/shared/tlc/TlcToolPanel.vue'
import type { TlcToolPanelWorkspaceDraft } from '@/shared/tlc/TlcToolPanel.vue'
import { isTlcSolventId, mapTlcReverseRange, type TlcCandidateSystem, type TlcReverseRange, type TlcSolventOption, type TlcTool } from '@/shared/tlc/types'

const props = withDefaults(defineProps<{ entry?: 'prediction' | 'utilities' }>(), { entry: 'prediction' })

const app = useAppStore()
const store = usePredictionStore()
const router = useRouter()
const route = useRoute()
const structureWorkspace: StructureWorkspaceContext = createStructureWorkspaceContext()
const selectedProjectId = ref('')
const structures = ref<Structure[]>([])
const selectedModelKey = ref('')
const restoredSmiles = ref('')
const reverseWorkspaceDraft = ref<TlcToolPanelWorkspaceDraft | null>(null)
const formError = ref<string | null>(null)
const loading = ref(false)
const submitting = ref(false)
const submissionLocked = ref(false)
const pollTimer = ref<number | null>(null)
let projectGeneration = 0
let submitAttemptGeneration = 0
let workspaceInitialized = false
let activeWorkspace: { key: string; actor: string; project: string; type: string; tool: string } | null = null
let workspaceValue: PredictionWorkspace | null = null
let workspaceGeneration = 0
const availableProjects = computed(() => app.accessContext?.accessible_projects ?? [])
const actorId = computed(() => app.accessContext?.actor_id ?? '')
const projectId = computed(() => resolveProjectId(app.accessContext, selectedProjectId.value) ?? '')
const projectState = computed(() => projectSelectionState(app.accessContext, selectedProjectId.value))
const serviceActionsUnavailable = computed(() => app.state !== 'ready')
const allowedActions = computed(() => app.accessContext?.allowed_actions ?? [])
const canReadPredictions = computed(() => allowedActions.value.includes('*') || allowedActions.value.includes('prediction:read'))
const canCreatePredictions = computed(() => allowedActions.value.includes('*') || allowedActions.value.includes('prediction:create'))
const projectContextKey = computed(() => {
  const context = app.accessContext
  if (!context) return ''
  return JSON.stringify([context.actor_id, context.actor?.policy_version, [...availableProjects.value.map((item) => item.id)].sort(), context.default_project_id, [...(context.permissions ?? [])].sort(), [...allowedActions.value].sort()])
})
const accessContextLoading = computed(() => !app.accessContext && (app.state === 'idle' || app.state === 'loading'))
const accessContextUnavailable = computed(() => !app.accessContext && app.state === 'error')
const healthyModels = computed(() => store.typeCapability?.models.filter((model) => model.health.status === 'READY') ?? [])
const selectedModel = computed(() => healthyModels.value.find((model) => `${model.id}::${model.version}` === selectedModelKey.value) ?? null)
const tlcOptions = computed(() => store.tlcOptions)
const solventOptions = computed<TlcSolventOption[]>(() => tlcOptions.value?.solvent_options.filter((item): item is TlcSolventOption => isTlcSolventId(item.id)) ?? [])
const candidateSystems = computed<TlcCandidateSystem[]>(() => tlcOptions.value?.candidate_systems ?? [])
const reverseRange = computed<TlcReverseRange>(() => mapTlcReverseRange(tlcOptions.value?.reverse))
const predictionResult = computed<PredictionResultPayload | null | undefined>(() => store.prediction?.result)
const reverseCurve = computed(() => mapTlcReverseCurve(predictionResult.value, store.prediction?.input_snapshot?.input, store.prediction?.status, Boolean(store.accepted && !store.prediction)))
const reverseResult = computed(() => predictionResult.value?.task_type === 'tlc_reverse_solvent' ? predictionResult.value : null)
const resultRows = computed(() => reverseResult.value?.candidates.map((candidate) => {
  const systemId = candidate.system_id
  const system = typeof systemId === 'string' ? candidateSystems.value.find((item) => item.id === systemId) : undefined
  const phase = candidate.mobile_phase
  const phaseEntries = phase && typeof phase === 'object' && !Array.isArray(phase) ? Object.entries(phase as Record<string, unknown>) : []
  const mobilePhase = phaseEntries.length
    ? phaseEntries.map(([solventId, fraction]) => {
      const option = solventOptions.value.find((item) => item.id === solventId)
      const solventLabel = option?.label?.trim() || solventId
      const percentage = typeof fraction === 'number' && Number.isFinite(fraction) && fraction >= 0 && fraction <= 1
        ? `${formatResultCell(fraction * 100, 'fraction')}%`
        : '—'
      return `${solventLabel} ${percentage}`
    }).join('；')
    : '—'
  return {
    system: system?.label?.trim() || (typeof systemId === 'string' ? systemId : '—'),
    mobilePhase,
    rf: typeof candidate.predicted_rf === 'number' && Number.isFinite(candidate.predicted_rf) ? formatResultCell(candidate.predicted_rf, 'predicted_rf') : '—',
    source: typeof candidate.source === 'string' && candidate.source ? candidate.source : '—',
  }
}) ?? [])
const resultError = computed(() => store.prediction && isPredictionTerminalStatus(store.prediction.status) ? resultMetadataError(store.prediction.result, store.prediction.chromatography_type) : null)
const resultStatus = computed(() => reverseResult.value?.target_reachable === false ? '目标不可达' : resultError.value ? '结果校验失败' : statusLabel(store.prediction?.status ?? ''))
const unavailableReason = computed(() => store.typeCapability?.health.status === 'UNAVAILABLE' ? store.typeCapability.health.reason ?? '服务端 TLC 能力不可用' : null)
const utilityReverseEnabled = activeProfile.utilityFeatures.includes('tlc_reverse_solvent') && activeProfile.chromatographyTypes.includes('tlc')
const predictionTools = computed(() => activeProfile.modules.includes('prediction')
  && activeProfile.chromatographyTypes.includes('tlc')
  ? (() => {
      const configured = (activeProfile.predictionTools.tlc ?? []).filter((tool): tool is TlcTool => ['single', 'batch', 'condition_compare', 'reverse_solvent'].includes(tool))
      const available = new Set<TlcTool>([...configured, 'batch'])
      return (['single', 'batch', 'condition_compare', 'reverse_solvent'] as const).filter((tool) => available.has(tool) && (tool === 'batch' || configured.includes(tool)))
    })()
  : [])
const rawStructureReady = computed(() => {
  const target = structureWorkspace.targets.get('tlc-reverse')
  return Boolean(target && target.projectId === projectId.value && target.getSmiles().trim())
})

watch([availableProjects, () => app.accessContext], ([projects, context]) => {
  const ids = projects.map((project) => project.id)
  selectedProjectId.value = ids.includes(selectedProjectId.value) ? selectedProjectId.value : (resolveProjectId(context, null) ?? '')
}, { immediate: true })

function stopPolling() { if (pollTimer.value !== null) window.clearTimeout(pollTimer.value); pollTimer.value = null }
function persistWorkspace() {
  if (!activeWorkspace) return
  const saved = readPredictionWorkspace(activeWorkspace.key) ?? emptyPredictionWorkspace(activeWorkspace.actor, activeWorkspace.project, activeWorkspace.type, activeWorkspace.tool)
  const task = store.prediction
  const taskMatches = task?.project_id === activeWorkspace.project && task.chromatography_type === activeWorkspace.type && task.tool === activeWorkspace.tool
  const accepted = taskMatches && store.accepted?.id === task!.id ? store.accepted : saved.accepted
  const value: PredictionWorkspace = {
    ...saved, smiles: restoredSmiles.value ? [restoredSmiles.value] : [''],
    form: reverseWorkspaceDraft.value ?? saved.form,
    modelKey: selectedModelKey.value || saved.modelKey,
    submissionLocked: submissionLocked.value,
    predictionId: taskMatches ? task!.id : saved.predictionId,
    accepted: accepted && (taskMatches ? accepted.id === task!.id : accepted.id === saved.predictionId) ? accepted : saved.accepted,
  }
  writePredictionWorkspace(activeWorkspace.key, value)
  workspaceValue = value
}
function saveAcceptedForWorkspace(key: string | undefined, accepted: PredictionWorkspace['accepted']) {
  if (!key || !accepted) return
  const saved = readPredictionWorkspace(key)
  if (!saved) return
  const value = { ...saved, predictionId: accepted.id, accepted, submissionLocked: false }
  writePredictionWorkspace(key, value)
  if (activeWorkspace?.key === key) workspaceValue = value
}
function setWorkspaceSubmissionLocked(key: string | undefined, locked: boolean) {
  if (!key) return
  const workspace = activeWorkspace?.key === key ? activeWorkspace : null
  const saved = readPredictionWorkspace(key) ?? (workspace ? emptyPredictionWorkspace(workspace.actor, workspace.project, workspace.type, workspace.tool) : null)
  if (!saved) return
  writePredictionWorkspace(key, { ...saved, submissionLocked: locked })
  if (activeWorkspace?.key === key) submissionLocked.value = locked
}
function updateStructureDraft(value: string) { restoredSmiles.value = value }
function activateWorkspace(actor: string, project: string) {
  if (!actor || !project) return
  const key = predictionWorkspaceKey(actor, project, 'tlc', 'reverse_solvent')
  if (activeWorkspace?.key === key) return
  persistWorkspace()
  workspaceGeneration += 1
  activeWorkspace = { key, actor, project, type: 'tlc', tool: 'reverse_solvent' }
  workspaceValue = readPredictionWorkspace(key) ?? emptyPredictionWorkspace(actor, project, 'tlc', 'reverse_solvent')
  restoredSmiles.value = workspaceValue.smiles[0] ?? ''
  reverseWorkspaceDraft.value = workspaceValue.form as TlcToolPanelWorkspaceDraft | null
  selectedModelKey.value = workspaceValue.modelKey
  submissionLocked.value = workspaceValue.submissionLocked
  structures.value = []
  store.prediction = null; store.accepted = null
}
function isCurrentProject(generation: number, expectedProjectId: string): boolean {
  return projectGeneration === generation && Boolean(expectedProjectId) && projectId.value === expectedProjectId && projectState.value === 'resolved'
}
function clearProjectState() {
  persistWorkspace()
  projectGeneration += 1
  submitAttemptGeneration += 1
  stopPolling()
  structures.value = []
  store.prediction = null
  store.accepted = null
  formError.value = null
  submitting.value = false
  loading.value = false
}
watch([projectId, projectContextKey], ([next, nextContext], [previous, previousContext]) => {
  if (next !== previous || nextContext !== previousContext) {
    clearProjectState()
    if (workspaceInitialized && next) void initialize()
  }
}, { flush: 'sync' })
watch([() => store.prediction, () => store.accepted, selectedModelKey, submissionLocked, restoredSmiles, reverseWorkspaceDraft], persistWorkspace, { deep: true })
function onNormalized(structure: Structure, slot: number) {
  if (structure.project_id !== projectId.value || projectState.value !== 'resolved') return
  const next = [...structures.value]; next[slot] = structure; structures.value = next
}
function openPredictionTool(tool: TlcTool) {
  if (!predictionTools.value.includes(tool)) return
  if (tool === 'reverse_solvent') return
  store.selectedType = 'tlc'
  store.selectedTool = tool
  void router.push({ path: '/prediction/tlc', query: { ...route.query, tool } })
}
function invalidateStructureSlot(slot: number) {
  if (slot !== 0) return
  structures.value = []
}

async function followPrediction(id: string) {
  const generation = projectGeneration
  const currentWorkspaceGeneration = workspaceGeneration
  const workspaceKey = activeWorkspace?.key
  const expectedProjectId = projectId.value
  stopPolling()
  try {
    const isCurrent = () => isCurrentProject(generation, expectedProjectId) && workspaceGeneration === currentWorkspaceGeneration
      && activeWorkspace?.key === workspaceKey && canReadPredictions.value
    const prediction = await store.getPrediction(id, isCurrent, expectedProjectId, 'tlc', (loaded) => loaded.id === id && loaded.tool === 'reverse_solvent')
    if (!isCurrent()) return
    if (prediction.id !== id || prediction.project_id !== expectedProjectId || prediction.chromatography_type !== 'tlc' || prediction.tool !== 'reverse_solvent') { store.prediction = null; store.accepted = null; return }
    if (!isPredictionTerminalStatus(prediction.status)) pollTimer.value = window.setTimeout(() => { void followPrediction(id) }, 1500)
  } catch (reason) { if (generation === projectGeneration && workspaceGeneration === currentWorkspaceGeneration) formError.value = reason instanceof Error ? reason.message : '预测状态读取失败' }
}

async function initialize() {
  let generation = projectGeneration
  loading.value = true; formError.value = null
  store.prediction = null; store.accepted = null
  try {
    if (!app.accessContext) await app.load()
    generation = projectGeneration
    const expectedProjectId = projectId.value
    activateWorkspace(actorId.value, expectedProjectId)
    const currentWorkspaceGeneration = workspaceGeneration
    const workspaceKey = activeWorkspace?.key
    const isCurrent = () => isCurrentProject(generation, expectedProjectId) && workspaceGeneration === currentWorkspaceGeneration && activeWorkspace?.key === workspaceKey
    const capability = await store.loadType('tlc', isCurrent)
    if (!isCurrent()) return
    if (capability.health.status === 'UNAVAILABLE') return
    const tool = capability.tools.find((item) => item.id === 'reverse_solvent')
    if (!tool) { formError.value = '当前构建或服务端未提供 TLC 反推工具。'; return }
    store.selectedType = 'tlc'; store.selectedTool = 'reverse_solvent'
    await store.loadTool('tlc', tool.id, tool.schema_version, isCurrent)
    if (!isCurrent()) return
    await store.loadTlcOptions(isCurrent)
    if (!isCurrent()) return
    const savedWorkspace = workspaceValue
    selectedModelKey.value = savedWorkspace?.modelKey
      ? healthyModels.value.some((model) => `${model.id}::${model.version}` === savedWorkspace.modelKey) ? savedWorkspace.modelKey : ''
      : healthyModels.value[0] ? `${healthyModels.value[0].id}::${healthyModels.value[0].version}` : ''
    const predictionId = savedWorkspace?.predictionId
    if (!predictionId || !canReadPredictions.value) return
    if (savedWorkspace.accepted?.id === predictionId) store.accepted = savedWorkspace.accepted
    const restored = await store.getPrediction(predictionId, () => isCurrent() && canReadPredictions.value,
      expectedProjectId, 'tlc', (loaded) => loaded.id === predictionId && loaded.tool === 'reverse_solvent')
    if (!isCurrent()) return
    if (restored.id !== predictionId || restored.project_id !== expectedProjectId || restored.chromatography_type !== 'tlc' || restored.tool !== 'reverse_solvent') { store.prediction = null; store.accepted = null; return }
    if (restored && !isPredictionTerminalStatus(restored.status)) await followPrediction(restored.id)
  } catch (reason) { if (projectGeneration === generation) formError.value = reason instanceof Error ? reason.message : 'TLC 反推能力暂不可用' }
  finally { if (projectGeneration === generation) loading.value = false }
}

async function submitReverse(value: { target_rf: { min: number; max: number }; candidate_system_ids: readonly string[]; ratio_step: number; max_candidates: number }) {
  if (submitting.value || submissionLocked.value) return
  const model = selectedModel.value
  if (!canCreatePredictions.value || unavailableReason.value || projectState.value !== 'resolved' || !projectId.value || !rawStructureReady.value || !store.toolSchema || !model) {
    formError.value = 'TLC 反推缺少健康服务端能力、已验证项目、结构输入或模型。'
    return
  }
  const generation = projectGeneration
  const expectedProjectId = projectId.value
  const expectedModelKey = selectedModelKey.value
  const attempt = ++submitAttemptGeneration
  const expectedWorkspaceGeneration = workspaceGeneration
  const expectedWorkspaceKey = activeWorkspace?.key
  const rawSmiles = structureWorkspace.targets.get('tlc-reverse')?.getSmiles() ?? ''
  const isCurrent = () => isCurrentProject(generation, expectedProjectId) && canCreatePredictions.value
    && selectedModelKey.value === expectedModelKey && submitAttemptGeneration === attempt
    && workspaceGeneration === expectedWorkspaceGeneration && activeWorkspace?.key === expectedWorkspaceKey
  const isCurrentNormalization = () => isCurrent()
    && structureWorkspace.targets.get('tlc-reverse')?.getSmiles() === rawSmiles
  submitting.value = true
  const structure = structures.value[0]?.project_id === expectedProjectId
    ? structures.value[0]
    : await structureWorkspace.targets.get('tlc-reverse')?.normalize() ?? null
  if (attempt !== submitAttemptGeneration) return
  if (!isCurrentNormalization()) { formError.value = '项目、模型或结构输入在校验过程中发生变化；本次反推已取消。'; submitting.value = false; return }
  if (!structure || structure.project_id !== expectedProjectId || !structure.id) {
    formError.value = '结构校验未通过；请检查分子结构，输入内容已保留，未创建反推任务。'
    submitting.value = false
    return
  }
  const structureRef = structure.id
  const body: ContractTlcReverseBody = {
    project_id: projectId.value,
    schema_version: store.toolSchema.schema_version,
    model_selection: { mode: 'exact', model_id: model.id, model_version: model.version },
    structure_ref: structureRef,
    target_rf_min: value.target_rf.min,
    target_rf_max: value.target_rf.max,
    candidate_system_ids: [...value.candidate_system_ids],
    ratio_step: value.ratio_step,
    max_candidates: value.max_candidates,
    chromatography_type: 'tlc',
    tool: 'reverse_solvent',
  }
  formError.value = null
  setWorkspaceSubmissionLocked(expectedWorkspaceKey, true)
  try { const accepted = await store.createTlcReverse(body, isCurrent); saveAcceptedForWorkspace(expectedWorkspaceKey, accepted.data); if (isCurrent()) await followPrediction(accepted.data.id) }
  catch (reason) {
    if (isCurrent()) {
      const rejected = reason instanceof ApiClientError && reason.status >= 400 && reason.status < 500 && reason.status !== 408
        if (rejected) setWorkspaceSubmissionLocked(expectedWorkspaceKey, false)
      submissionLocked.value = !rejected
      formError.value = rejected ? (reason instanceof Error ? reason.message : 'TLC 反推提交失败') : 'TLC 反推提交状态未确认；请先核对预测记录，当前不会重复提交。'
    }
  }
  finally { if (isCurrent()) submitting.value = false }
}

onMounted(async () => { await initialize(); workspaceInitialized = true })
onUnmounted(() => { persistWorkspace(); projectGeneration += 1; submitAttemptGeneration += 1; stopPolling() })
</script>

<template>
  <div class="view tlc-reverse-workspace">
    <header class="page-header"><span class="eyebrow">TLC REVERSE SOLVENT</span><h1>TLC 溶剂反推</h1><p>预测入口与实用工具使用同一能力、项目、正式提交和任务恢复流程。当前构建 {{ activeProfile.id }}。</p></header>
    <nav v-if="props.entry === 'prediction' && predictionTools.length" class="panel workspace-nav tlc-tool-nav" aria-label="TLC 工具"><button v-for="tool in predictionTools" :key="tool" type="button" :class="{ active: tool === 'reverse_solvent' }" @click="openPredictionTool(tool)">{{ tool === 'single' ? '单次预测' : tool === 'batch' ? '批量预测' : tool === 'condition_compare' ? '条件比较' : '反推溶剂' }}</button></nav>
    <nav class="panel workspace-nav utility-nav" :aria-label="props.entry === 'utilities' ? '实用工具入口' : '导航'"><RouterLink to="/structure">结构工具</RouterLink><RouterLink v-if="utilityReverseEnabled" to="/utilities/tlc-reverse">实用工具 · TLC 反推</RouterLink><RouterLink v-if="activeProfile.modules.includes('prediction')" to="/prediction/tlc">色谱预测 · TLC</RouterLink></nav>
    <section class="panel project-section"><label>项目<select v-model="selectedProjectId" :disabled="!app.accessContext || loading || submitting" data-testid="tlc-reverse-project"><option value="" disabled>选择服务端访问上下文中的项目</option><option v-for="project in availableProjects" :key="project.id" :value="project.id">{{ project.name || project.id }}</option></select></label><p v-if="projectState === 'needs_selection'" class="error-text">请选择项目；不会使用未验证的默认项目。</p></section>
    <section v-if="accessContextLoading" class="panel placeholder" data-testid="access-context-loading" role="status" aria-live="polite">正在读取访问上下文…反推功能暂不可提交。</section>
    <section v-else-if="accessContextUnavailable" class="panel error-text" data-testid="access-context-error" role="alert">访问上下文暂不可用，反推功能已禁用。请检查服务连接后重试。</section>
    <section v-else-if="projectState === 'blocked'" class="panel error-text" data-testid="no-accessible-projects" role="status">服务端没有为当前访问上下文分配可访问项目，反推暂不可提交。</section>
    <section v-else-if="!canCreatePredictions" class="panel error-text" data-testid="prediction-permission-blocked" role="status">当前访问上下文没有预测创建权限，反推暂不可提交。</section>
    <section v-else-if="unavailableReason" class="panel empty-state"><h2>服务端 TLC 能力不可用</h2><p>原因：{{ unavailableReason }}</p></section>
    <section v-else-if="loading" class="panel placeholder">正在读取 TLC 反推能力、模型和条件…</section>
    <template v-else>
      <StructureWorkspace :context="structureWorkspace"><section class="panel structure-section"><div class="section-heading"><div><span class="eyebrow">MOLECULE INPUT</span><h2>分子结构</h2></div><p>点击提交时自动调用服务端校验，成功后以结构引用提交；也可提前校验。</p></div><StructureInput v-if="projectId" :key="`${projectId}:${workspaceGeneration}`" target-id="tlc-reverse" :initial-smiles="restoredSmiles" :project-id="projectId" :disabled="serviceActionsUnavailable" @draft-change="updateStructureDraft" @normalized="onNormalized($event, 0)" @invalidated="invalidateStructureSlot(0)" /></section><SharedKetcherCanvas /></StructureWorkspace>
      <section class="panel model-strip"><label>预测模型<select v-model="selectedModelKey" :disabled="submitting || submissionLocked"><option value="" disabled>请选择健康模型</option><option v-for="model in healthyModels" :key="`${model.id}::${model.version}`" :value="`${model.id}::${model.version}`">{{ model.task_type }} · {{ model.version }} · {{ model.scope }}</option></select></label><p v-if="!healthyModels.length" class="error-text">服务端未返回可用真实模型。</p></section>
      <TlcToolPanel v-if="tlcOptions" :key="workspaceGeneration" model-value="reverse_solvent" :initial-draft="reverseWorkspaceDraft" :visible-tools="['reverse_solvent']" :hide-navigation="true" :structure-input-ready="rawStructureReady" :busy="submitting || submissionLocked" :structure-ref="structures[0]?.id ?? null" :solvent-options="solventOptions" :candidate-systems="candidateSystems" :reverse-range="reverseRange" :points="reverseCurve.points" :initial-rf="reverseCurve.initialRf" :result-state="reverseCurve.resultState" :failed-point-count="reverseCurve.failedPointCount" :result-message="reverseCurve.resultMessage" @draft-change="reverseWorkspaceDraft = $event" @submit-reverse="submitReverse" />
      <section v-else class="panel empty-state"><h2>服务端 TLC 反推能力尚未就绪</h2><p>服务端尚未返回合法候选体系与范围；不会显示静态候选值。</p></section>
      <p v-if="formError" class="panel error-text">{{ formError }}</p>
      <p v-if="submissionLocked && !store.accepted && !store.prediction" class="panel warning-text" data-testid="submission-status-unknown">上次提交状态尚未确认，请先到预测记录核对；不会自动重复提交。</p>
    <section class="panel result-panel reverse-result-panel"><div class="section-heading"><div><span class="eyebrow">REAL RESULT</span><h2>正式反推结果</h2></div><span v-if="store.prediction" class="runtime-pill">{{ resultStatus }}</span></div><div v-if="store.accepted && !store.prediction" class="placeholder">任务已提交，正在读取服务端结果…</div><div v-else-if="!store.prediction" class="placeholder">提交后显示服务端正式候选；这里不会展示演示数据。</div><template v-else><dl v-if="!resultError" class="facts"><div><dt>状态</dt><dd>{{ statusLabel(store.prediction.status) }}</dd></div><div><dt>模型版本</dt><dd>{{ store.prediction.model.version }}</dd></div><div><dt>更新时间</dt><dd>{{ store.prediction.updated_at }}</dd></div></dl><p v-if="store.prediction.error" class="error-text">{{ store.prediction.error.message }}</p><p v-if="resultError" class="error-text">{{ resultError }}</p><div v-if="reverseResult?.target_reachable === false" class="empty-state"><strong>目标 Rf 不可达</strong><p>服务端未返回满足目标范围的候选溶剂体系。</p></div><div v-if="!resultError && resultRows.length" class="reverse-result-table-wrap" role="region" aria-label="正式反推候选结果" tabindex="0"><table class="reverse-result-table"><thead><tr><th>候选体系</th><th>展开剂（体积分数）</th><th>Rf</th><th>结果来源</th></tr></thead><tbody><tr v-for="(row, index) in resultRows" :key="index"><td>{{ row.system }}</td><td>{{ row.mobilePhase }}</td><td>{{ row.rf }}</td><td>{{ row.source }}</td></tr></tbody></table></div><div v-if="store.prediction.warnings?.length" class="event-list"><p v-for="warning in store.prediction.warnings" :key="warning.code">{{ warning.message }}</p></div><div v-if="reverseResult?.warnings.length" class="event-list"><p v-for="warning in reverseResult.warnings" :key="warning">{{ warning }}</p></div></template></section>
    </template>
  </div>
</template>

<style scoped>
.workspace-nav{display:flex;gap:8px;flex-wrap:wrap}.workspace-nav a,.workspace-nav button{border:1px solid var(--line);border-radius:8px;padding:8px 12px;color:var(--primary-deep);text-decoration:none;background:#fff;font:inherit;cursor:pointer}.workspace-nav a.router-link-active,.workspace-nav button.active{background:var(--primary);color:#fff}.utility-nav{padding:0;border:0;background:transparent}
.reverse-result-panel{min-width:0}.reverse-result-table-wrap{max-width:100%;overflow-x:auto}.reverse-result-table{width:100%;min-width:640px}
</style>
