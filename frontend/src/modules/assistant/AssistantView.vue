<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useAppStore } from '@/stores/app'
import { useConfirmationStore } from '@/stores/confirmations'
import { assistantFailureNotice, assistantPredictionResourceId, assistantRegistrationDraftResourceId, assistantToolGroup, canSendAssistantMessage, makeResumeBody, structureToolSummary, toolResultSummaryEntries, useAssistantStore } from './store'
import { statusLabel } from '@/core/status-labels'
import { ApiClientError, createIdempotencyKey } from '@/core/api/client'
import type { ContractConversation, ContractConversationDelete, ContractDataDomain, ContractDataVersionSummary, ContractMessage, ContractPrediction, ContractPredictionRecordSummary, ContractTypeSummary } from '@/core/contract-types'
import { resultMetadataError, toDisplayRow } from '@/shared/prediction/result'
import AssistantText from './AssistantText.vue'

const store = useAssistantStore()
const app = useAppStore()
const confirmationStore = useConfirmationStore()
const draft = ref('')
const pendingUserEcho = ref<{ conversationId: string; content: string } | null>(null)
const error = ref<string | null>(null)
const busy = ref(false)
const refreshing = ref(false)
const predictionLoading = ref<Record<string, boolean>>({})
const dataDomains = ref<ContractDataDomain[]>([])
const predictionTypes = ref<ContractTypeSummary[]>([])
const domainCapabilityState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const typeCapabilityState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const domainCapabilityError = ref<string | null>(null)
const typeCapabilityError = ref<string | null>(null)
const selectedSummaryDomain = ref('')
const selectedSummaryType = ref('')
const sidebarCollapsed = ref(false)
const mobileSidebarOpen = ref(false)
const mobileMenuTrigger = ref<HTMLButtonElement | null>(null)
const assistantPage = ref<HTMLDivElement | null>(null)
const showArchived = ref(false)
const conversationQuery = ref('')
const conversationSearchBusy = ref(false)
const conversationHasMore = ref(false)
const conversationCursor = ref<string | null>(null)
let conversationListGeneration = 0
let conversationListRequest = 0
const renameId = ref<string | null>(null)
const renameDraft = ref('')
const archiveTarget = ref<(typeof visibleConversations.value)[number] | null>(null)
const archiveLoading = ref(false)
const archiveNotice = ref<string | null>(null)
const archiveDialog = ref<HTMLElement | null>(null)
const archiveTrigger = ref<HTMLElement | null>(null)
const deleteTarget = ref<ContractConversation | null>(null)
const deleteLoading = ref(false)
const deleteNotice = ref<string | null>(null)
const deleteDialog = ref<HTMLElement | null>(null)
const deleteTrigger = ref<HTMLElement | null>(null)
const deleteAttempt = ref<{ targetId: string; etag: string; confirmationId: string | null; idempotencyKey: string | null; projectId: string; wasSelected: boolean; adjacentId: string | null } | null>(null)
let deleteRequestGeneration = 0
let deleteBusyOwner = 0
const conversationSearchInput = ref<HTMLInputElement | null>(null)
const messageScroll = ref<HTMLElement | null>(null)
const composerInput = ref<HTMLTextAreaElement | null>(null)
const atMessageBottom = ref(true)
const showJumpLatest = ref(false)
const composing = ref(false)
let conversationSearchTimer: ReturnType<typeof setTimeout> | null = null
const dataVersionSummary = ref<ContractDataVersionSummary | null>(null)
const predictionRecordSummary = ref<ContractPredictionRecordSummary | null>(null)
const dataVersionSummaryState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const predictionRecordSummaryState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
const dataVersionSummaryError = ref<string | null>(null)
const predictionRecordSummaryError = ref<string | null>(null)
let summaryCapabilityRequest = 0
let dataVersionSummaryRequest = 0
let predictionRecordSummaryRequest = 0
let summaryCapabilityContext = ''
let summaryViewMounted = false

const selectedConversation = computed(() => [...store.conversations, ...store.archivedConversations].find((item) => item.id === store.selectedConversationId) ?? null)
const conversationModeLabel = computed(() => selectedConversation.value?.mode === 'knowledge' ? '知识模式' : selectedConversation.value?.mode === 'business' ? '业务模式' : selectedConversation.value ? '未知模式' : '业务模式')
const activeRun = computed(() => store.run)
const ACTIVE_CONVERSATION_RUN_STATUSES = new Set(['QUEUED', 'RUNNING', 'WAITING_CONFIRMATION', 'CANCEL_REQUESTED'])
const TERMINAL_RUN_STATUSES = new Set(['SUCCEEDED', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'])
function hasLoadedActiveRun(conversation: (typeof store.conversations)[number]) {
  const run = store.run
  return Boolean(conversation.current_run_id && run && run.id === conversation.current_run_id && run.conversation_id === conversation.id && ACTIVE_CONVERSATION_RUN_STATUSES.has(run.status))
}
const incompleteAnswerMessage = computed(() => ['SUCCEEDED', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'].includes(activeRun.value?.status ?? '')
  ? '运行已结束，保留未完成文本'
  : '回答尚未完整；等待服务端最终消息')
const failureNotice = computed(() => assistantFailureNotice(activeRun.value, store.events?.events ?? [], store.liveEvents))
const canStop = computed(() => Boolean(activeRun.value && !['SUCCEEDED', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'].includes(activeRun.value.status)))
const canSend = computed(() => selectedConversation.value?.status === 'ACTIVE' && canSendAssistantMessage({ projectId: store.projectId, projectSelection: store.accessContext?.project_selection, conversationId: selectedConversation.value?.id ?? null, draft: draft.value, runStatus: activeRun.value?.status }))
const visibleConversations = computed(() => showArchived.value ? store.archivedConversations : conversationQuery.value.trim() ? store.searchResults : store.conversations)
const conversationGroups = computed(() => {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const groups = new Map<string, typeof visibleConversations.value>()
  for (const conversation of visibleConversations.value) {
    const date = new Date(conversation.last_message_at || conversation.updated_at); date.setHours(0, 0, 0, 0)
    const days = Math.floor((today.getTime() - date.getTime()) / 86_400_000)
    const group = days <= 0 ? '今天' : days === 1 ? '昨天' : days <= 7 ? '近 7 天' : '更早'
    groups.set(group, [...(groups.get(group) ?? []), conversation])
  }
  return [...groups.entries()]
})
const pendingEchoIsVisible = computed(() => Boolean(pendingUserEcho.value && pendingUserEcho.value.conversationId === store.selectedConversationId && !store.messages.some((message) => message.role === 'user' && (message.blocks ?? []).some((block) => block.type === 'text' && block.text === pendingUserEcho.value?.content))))
const canQueryProjectSummary = computed(() => Boolean(store.projectId && store.accessContext?.project_selection === 'resolved'))
const pendingActions = computed(() => activeRun.value?.pending_actions ?? [])
const pendingConfirmation = computed(() => pendingActions.value.length === 1 && pendingActions.value[0]?.confirmation_id ? pendingActions.value[0] : null)
const pendingConfirmationBlocked = computed(() => pendingActions.value.length > 1)
const projectedOnlyMessages = computed(() => {
  const groups = new Map<string, { id: string; blocks: string[]; incomplete: boolean }>()
  for (const projection of store.streamTextProjections) {
    if (projection.conversation_id !== store.selectedConversationId || projection.run_id !== activeRun.value?.id || store.messages.some((message) => message.id === projection.message_id)) continue
    const group = groups.get(projection.message_id) ?? { id: projection.message_id, blocks: [], incomplete: true }
    group.blocks[projection.block_index] = projection.text
    group.incomplete ||= projection.incomplete
    groups.set(projection.message_id, group)
  }
  return [...groups.values()]
})
const visibleToolCalls = computed(() => store.toolCalls.filter((call) => assistantToolGroup(call.tool_name) !== null))
const successfulToolCalls = computed(() => visibleToolCalls.value.filter((call) => call.status === 'SUCCEEDED'))
const hasDataSummary = computed(() => successfulToolCalls.value.some((call) => assistantToolGroup(call.tool_name) === 'data' && !isExecutionMetadata(call) && summaryEntries(call).length > 0))
const registrationDraftToolNames = new Set(['create_registration_draft', 'get_registration_draft'])
const hasModelResult = computed(() => successfulToolCalls.value.some((call) => assistantToolGroup(call.tool_name) === 'model' && !registrationDraftToolNames.has(call.tool_name) && !isExecutionMetadata(call) && summaryEntries(call).length > 0))
const hasPredictionResult = computed(() => store.messages.some((message) => (message.blocks ?? []).some((block) => block.type === 'prediction_result')))
const firstRoundToolNames = new Set(['normalize_structure', 'get_prediction_capabilities', 'create_prediction', 'get_prediction', 'get_data_version_summary', 'get_prediction_record_summary', 'list_models', 'get_model_card', 'create_registration_draft', 'get_registration_draft'])
const firstRoundTools = computed(() => store.priorityTools.filter((tool) => firstRoundToolNames.has(tool.name)))
const registrationDraftReferences = computed(() => {
  if (!store.projectId || store.accessContext?.project_selection !== 'resolved') return []
  const seen = new Set<string>()
  return successfulToolCalls.value.flatMap((call) => {
    const id = assistantRegistrationDraftResourceId(call)
    if (!id || seen.has(id)) return []
    seen.add(id)
    return [{ call, id }]
  })
})
const predictionReferences = computed(() => {
  if (!store.projectId || store.accessContext?.project_selection !== 'resolved') return []
  const seen = new Set<string>()
  return successfulToolCalls.value.flatMap((call) => {
    const id = assistantPredictionResourceId(call)
    if (!id || seen.has(id)) return []
    seen.add(id)
    return [{ call, id }]
  })
})
const summaryToolCalls = computed(() => successfulToolCalls.value.filter((call) => !registrationDraftToolNames.has(call.tool_name) && call.result_summary))

function publicSummaryError(reason: unknown, fallback: string): string {
  if (reason instanceof ApiClientError) return `${reason.code}: ${reason.message}`
  return reason instanceof Error ? reason.message : fallback
}

function clearDataVersionSummary() {
  dataVersionSummaryRequest += 1
  dataVersionSummary.value = null
  dataVersionSummaryError.value = null
  dataVersionSummaryState.value = 'idle'
}

function clearPredictionRecordSummary() {
  predictionRecordSummaryRequest += 1
  predictionRecordSummary.value = null
  predictionRecordSummaryError.value = null
  predictionRecordSummaryState.value = 'idle'
}

function clearProjectSummaries() {
  clearDataVersionSummary()
  clearPredictionRecordSummary()
}

function isCurrentSummaryContext(projectId: string, conversationId: string | null) {
  return summaryViewMounted && store.projectId === projectId && store.selectedConversationId === conversationId && store.accessContext?.project_selection === 'resolved'
}

function isCurrentSummaryProject(projectId: string) {
  return summaryViewMounted && store.projectId === projectId && store.accessContext?.project_selection === 'resolved'
}

async function loadSummaryCapabilities(force = false) {
  if (!summaryViewMounted) return
  const requestProjectId = store.projectId
  const resolved = store.accessContext?.project_selection === 'resolved'
  const contextKey = `${requestProjectId ?? ''}:${resolved}`
  if (!force && contextKey === summaryCapabilityContext) return
  const requestId = ++summaryCapabilityRequest
  summaryCapabilityContext = contextKey
  dataDomains.value = []
  predictionTypes.value = []
  selectedSummaryDomain.value = ''
  selectedSummaryType.value = ''
  domainCapabilityError.value = null
  typeCapabilityError.value = null
  if (!requestProjectId || !resolved) {
    domainCapabilityState.value = 'idle'
    typeCapabilityState.value = 'idle'
    return
  }
  domainCapabilityState.value = 'loading'
  typeCapabilityState.value = 'loading'
  const [domainsResult, typesResult] = await Promise.allSettled([store.listDataDomains(), store.listPredictionTypes()])
  if (requestId !== summaryCapabilityRequest || !isCurrentSummaryProject(requestProjectId)) return
  if (domainsResult.status === 'fulfilled') {
    dataDomains.value = domainsResult.value.data
    domainCapabilityState.value = 'ready'
  } else {
    domainCapabilityState.value = 'error'
    domainCapabilityError.value = publicSummaryError(domainsResult.reason, '数据域目录读取失败')
  }
  if (typesResult.status === 'fulfilled') {
    predictionTypes.value = typesResult.value
    typeCapabilityState.value = 'ready'
  } else {
    typeCapabilityState.value = 'error'
    typeCapabilityError.value = publicSummaryError(typesResult.reason, '色谱类型目录读取失败')
  }
}

function validateDataVersionSummary(summary: ContractDataVersionSummary, projectId: string, domainId: string): string | null {
  if (summary.project_id !== projectId) return '服务端返回的数据版本摘要不属于当前项目。'
  if (summary.source?.source_type !== 'data_version') return '服务端返回的数据版本摘要来源类型不匹配。'
  if (summary.domain_id != null && summary.domain_id !== domainId) return '服务端返回的数据版本摘要与所选数据域不匹配。'
  return null
}

function validatePredictionRecordSummary(summary: ContractPredictionRecordSummary, projectId: string, chromatographyType: string): string | null {
  if (summary.project_id !== projectId) return '服务端返回的预测记录摘要不属于当前项目。'
  if (summary.source?.source_type !== 'prediction_record') return '服务端返回的预测记录摘要来源类型不匹配。'
  if (summary.chromatography_type != null && summary.chromatography_type !== chromatographyType) return '服务端返回的预测记录摘要与所选色谱类型不匹配。'
  return null
}

async function queryDataVersionSummary() {
  if (!canQueryProjectSummary.value || domainCapabilityState.value !== 'ready' || !dataDomains.value.some((domain) => domain.id === selectedSummaryDomain.value) || dataVersionSummaryState.value === 'loading') return
  const projectId = store.projectId!
  const conversationId = store.selectedConversationId
  const domainId = selectedSummaryDomain.value
  const requestId = ++dataVersionSummaryRequest
  dataVersionSummary.value = null
  dataVersionSummaryError.value = null
  dataVersionSummaryState.value = 'loading'
  try {
    const summary = await store.getDataVersionSummary(domainId)
    if (requestId !== dataVersionSummaryRequest || !isCurrentSummaryContext(projectId, conversationId) || selectedSummaryDomain.value !== domainId) return
    const invalid = validateDataVersionSummary(summary, projectId, domainId)
    if (invalid) throw new Error(invalid)
    dataVersionSummary.value = summary
    dataVersionSummaryState.value = 'ready'
  } catch (reason) {
    if (requestId !== dataVersionSummaryRequest || !isCurrentSummaryContext(projectId, conversationId) || selectedSummaryDomain.value !== domainId) return
    dataVersionSummaryError.value = publicSummaryError(reason, '数据版本摘要查询失败')
    dataVersionSummaryState.value = 'error'
  }
}

async function queryPredictionRecordSummary() {
  if (!canQueryProjectSummary.value || (selectedSummaryType.value && (typeCapabilityState.value !== 'ready' || !predictionTypes.value.some((type) => type.id === selectedSummaryType.value))) || predictionRecordSummaryState.value === 'loading') return
  const projectId = store.projectId!
  const conversationId = store.selectedConversationId
  const chromatographyType = selectedSummaryType.value
  const requestId = ++predictionRecordSummaryRequest
  predictionRecordSummary.value = null
  predictionRecordSummaryError.value = null
  predictionRecordSummaryState.value = 'loading'
  try {
    const summary = await store.getPredictionRecordSummary(chromatographyType || undefined)
    if (requestId !== predictionRecordSummaryRequest || !isCurrentSummaryContext(projectId, conversationId) || selectedSummaryType.value !== chromatographyType) return
    const invalid = validatePredictionRecordSummary(summary, projectId, chromatographyType)
    if (invalid) throw new Error(invalid)
    predictionRecordSummary.value = summary
    predictionRecordSummaryState.value = 'ready'
  } catch (reason) {
    if (requestId !== predictionRecordSummaryRequest || !isCurrentSummaryContext(projectId, conversationId) || selectedSummaryType.value !== chromatographyType) return
    predictionRecordSummaryError.value = publicSummaryError(reason, '预测记录摘要查询失败')
    predictionRecordSummaryState.value = 'error'
  }
}

type SummaryDto = ContractDataVersionSummary | ContractPredictionRecordSummary
type NumericStat = NonNullable<ContractDataVersionSummary['numeric']>[string]
const UNSAFE_SUMMARY_KEY = /token|secret|password|path|stack|trace|authorization|api.?key/i
const SAFE_SUMMARY_KEY = /^[A-Za-z][A-Za-z0-9_.-]{0,95}$/
const SAFE_PUBLIC_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/

function safeSummaryKey(key: string) { return SAFE_SUMMARY_KEY.test(key) && !UNSAFE_SUMMARY_KEY.test(key) }
function summaryNumericEntries(summary: SummaryDto | null): [string, NumericStat][] {
  if (!summary) return []
  return Object.entries(summary.numeric ?? {}).filter(([key, stat]) => safeSummaryKey(key) && stat && Number.isFinite(stat.count) && stat.count >= 0).slice(0, 6) as [string, NumericStat][]
}
function summaryCountEntries(summary: SummaryDto | null, field: 'status_counts' | 'missing'): [string, number][] {
  if (!summary) return []
  return Object.entries(summary[field] ?? {}).filter(([key, count]) => safeSummaryKey(key) && Number.isFinite(count) && count >= 0).slice(0, 6) as [string, number][]
}
function summaryUnitEntries(summary: SummaryDto | null): [string, string][] {
  if (!summary) return []
  return Object.entries(summary.units ?? {}).filter(([key, unit]) => safeSummaryKey(key) && safeSummaryUnit(unit)).slice(0, 6).map(([key, unit]) => [key, unit as string])
}
function safeSummaryUnit(value: unknown): value is string { return typeof value === 'string' && value.length <= 40 && !/[\u0000-\u001f\u007f\\]/.test(value) && !/^\//.test(value) && !/(?:api.?key|secret|token|password)/i.test(value) }
function summaryStatUnit(value: unknown) { return safeSummaryUnit(value) ? value : null }
function summarySourceIds(summary: SummaryDto | null): string[] {
  if (!summary || !Array.isArray(summary.source?.source_ids)) return []
  return summary.source.source_ids.filter((id) => typeof id === 'string' && SAFE_PUBLIC_ID.test(id)).slice(0, 10)
}
function summarySourceIdsOmitted(summary: SummaryDto | null): number {
  if (!summary || !Array.isArray(summary.source?.source_ids)) return 0
  return Math.max(0, summary.source.source_ids.filter((id) => typeof id === 'string' && SAFE_PUBLIC_ID.test(id)).length - 10)
}
function formatSummaryNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? String(Number(value.toPrecision(5))) : '—'
}

watch(() => [store.projectId, store.accessContext?.project_selection] as const, () => {
  clearProjectSummaries()
  selectedSummaryDomain.value = ''
  selectedSummaryType.value = ''
  summaryCapabilityContext = ''
  summaryCapabilityRequest += 1
  dataDomains.value = []
  predictionTypes.value = []
  domainCapabilityError.value = null
  typeCapabilityError.value = null
  domainCapabilityState.value = 'idle'
  typeCapabilityState.value = 'idle'
  if (summaryViewMounted && canQueryProjectSummary.value) void loadSummaryCapabilities()
}, { flush: 'sync' })

watch(() => store.selectedConversationId, clearProjectSummaries, { flush: 'sync' })
watch(() => store.projectId, (projectId, previousProjectId) => {
  if (projectId !== previousProjectId && deleteTarget.value) {
    invalidateDeleteBusyOwner()
    discardDeleteDialog(false)
  }
}, { flush: 'sync' })
watch(selectedSummaryDomain, clearDataVersionSummary, { flush: 'sync' })
watch(selectedSummaryType, clearPredictionRecordSummary, { flush: 'sync' })

interface AssistantPredictionDetail {
  status: string
  chromatographyType: string
  kind: 'pending' | 'error' | 'missing' | 'invalid' | 'tlc' | 'other'
  errorCode: string | null
  errorMessage: string | null
  modelId: string | null
  modelVersion: string | null
  artifactSha256: string | null
  rfValues: string[]
  warnings: string[]
}

function formatTime(value?: string | null) {
  if (!value) return '刚刚'
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

function toolLabel(name: string) {
  const labels: Record<string, string> = { create_prediction: '提交预测', get_prediction: '查询预测结果', get_prediction_capabilities: '查询预测能力', normalize_structure: '规范化并校验结构', create_batch_draft: '创建批量草稿', update_batch_draft: '更新批量草稿', validate_batch: '校验批量任务', submit_batch: '提交批量任务', get_batch: '查询批量任务', tlc_reverse_solvent: 'TLC 反推溶剂', get_data_summary: '已有数据摘要', get_data_version_summary: '数据版本摘要', get_prediction_summary: '预测记录摘要', get_prediction_record_summary: '预测记录摘要', list_data_versions: '数据版本摘要', list_prediction_records: '预测记录摘要', list_models: '模型列表', get_model_card: '查询模型卡', create_registration_draft: '创建登记草稿', get_registration_draft: '查询登记草稿', validate_registration: '查询登记草稿' }
  return labels[name] ?? name
}

type AssistantBlock = NonNullable<ContractMessage['blocks']>[number]
function blockText(block: AssistantBlock) { return block.type === 'text' ? block.text : '' }
function displayedBlockText(messageId: string, index: number, block: AssistantBlock) {
  if (block.type !== 'text') return ''
  const projection = store.streamTextProjections.find((item) => item.conversation_id === store.selectedConversationId && item.run_id === activeRun.value?.id && item.message_id === messageId && item.block_index === index)
  return projection?.text ?? block.text
}
function projectedTextBlocks(blocks: string[]) { return blocks.flatMap((text, index) => text === undefined ? [] : [{ text, index }]) }
function messageHasIncompleteProjection(messageId: string) {
  return store.streamTextProjections.some((item) => item.conversation_id === store.selectedConversationId && item.run_id === activeRun.value?.id && item.message_id === messageId && item.incomplete)
}
function additionalProjectedBlocks(message: ContractMessage) {
  return store.streamTextProjections
    .filter((item) => item.conversation_id === store.selectedConversationId && item.run_id === activeRun.value?.id && item.message_id === message.id && message.blocks?.[item.block_index]?.type !== 'text')
    .map((item) => ({ index: item.block_index, text: item.text }))
}
function blockId(block: AssistantBlock) { return block.type === 'prediction_result' ? block.prediction_id : '' }
function summaryEntries(call: (typeof store.toolCalls)[number]) {
  return toolResultSummaryEntries(call)
}
function summaryValue(value: unknown) { return typeof value === 'string' ? value : String(value) }
function structureSummary(call: (typeof store.toolCalls)[number]) { return structureToolSummary(call.result_summary) }
function isExecutionMetadata(call: (typeof store.toolCalls)[number]) {
  return Boolean(call.result_summary && typeof call.result_summary === 'object' && call.result_summary.metadata_only === true)
}

function predictionWarnings(prediction: ContractPrediction): string[] {
  const rawWarnings: unknown[] = Array.isArray(prediction.warnings) ? [...prediction.warnings] : []
  const result = prediction.result as unknown as { task_type?: string; applicability?: { warnings?: unknown } } | null
  if (result?.task_type === 'tlc' && Array.isArray(result.applicability?.warnings)) rawWarnings.push(...result.applicability.warnings)
  const combined = rawWarnings
  return [...new Set(combined.flatMap((warning) => {
    if (!warning || typeof warning !== 'object' || Array.isArray(warning)) return []
    const record = warning as { code?: unknown; message?: unknown }
    if (typeof record.message !== 'string' || !record.message.trim()) return []
    return [typeof record.code === 'string' && record.code ? `${record.code}：${record.message}` : record.message]
  }))].slice(0, 12)
}

function predictionDetail(id: string): AssistantPredictionDetail | null {
  const prediction = store.predictionResults[id]
  if (!prediction) return null
  const base = { status: statusLabel(prediction.status), chromatographyType: prediction.chromatography_type.toUpperCase(), modelId: null, modelVersion: null, artifactSha256: null, rfValues: [], warnings: predictionWarnings(prediction) }
  if (prediction.status !== 'SUCCEEDED') {
    const failure = assistantFailureNotice({ error: prediction.error })
    return { ...base, kind: 'pending', errorCode: failure?.code ?? null, errorMessage: failure?.summary ?? null }
  }
  if (!prediction.result) {
    const failure = assistantFailureNotice({ error: prediction.error })
    return { ...base, kind: 'missing', errorCode: failure?.code ?? null, errorMessage: failure?.summary ?? '服务端未返回正式结果数据。' }
  }
  const resultError = resultMetadataError(prediction.result, prediction.chromatography_type)
  if (resultError) return { ...base, kind: 'invalid', errorCode: 'RESULT_CONTRACT_INVALID', errorMessage: resultError }
  if (prediction.chromatography_type !== 'tlc' || prediction.result.task_type !== 'tlc') {
    return { ...base, kind: 'other', errorCode: null, errorMessage: null }
  }
  const rows = prediction.result.rows.map((row) => toDisplayRow(row))
  if (!rows.length || rows.some((row) => typeof row.rf !== 'number' || !Number.isFinite(row.rf) || row.rf < 0 || row.rf > 1)) {
    return { ...base, kind: 'invalid', errorCode: 'RESULT_CONTRACT_INVALID', errorMessage: rows.length ? 'TLC 结果包含无效 Rf 值，已隐藏结果。' : 'TLC 结果未返回 Rf 行，已隐藏结果。' }
  }
  const rfValues = rows.map((row) => {
    const rf = row.rf as number
    return String(rf)
  })
  return { ...base, kind: 'tlc', errorCode: null, errorMessage: null, modelId: prediction.result.model_id, modelVersion: prediction.result.model_version, artifactSha256: prediction.result.artifact_sha256, rfValues }
}

async function refresh() {
  refreshing.value = true; error.value = null
  try { await store.bootstrap(); await loadSummaryCapabilities() } catch (reason) { error.value = reason instanceof Error ? reason.message : '刷新助手工作区失败' } finally { refreshing.value = false }
}

async function newConversation() {
  if (deleteTarget.value) return
  error.value = null; busy.value = true
  try { await store.createConversation() } catch (reason) { error.value = reason instanceof Error ? reason.message : '新会话创建失败' } finally { busy.value = false }
}

async function chooseConversation(id: string) {
  if (busy.value || deleteTarget.value) return
  error.value = null; busy.value = true
  try { await store.loadConversation(id); mobileSidebarOpen.value = false } catch (reason) { error.value = reason instanceof Error ? reason.message : '会话恢复失败' } finally { busy.value = false }
}

function closeMobileSidebar() {
  if (!mobileSidebarOpen.value) return
  mobileSidebarOpen.value = false
  void nextTick(() => mobileMenuTrigger.value?.focus({ preventScroll: true }))
}

function closeConversationMenu(event?: Event) {
  const menu = event?.currentTarget instanceof HTMLElement ? event.currentTarget.closest('details.conversation-menu') : null
  if (!(menu instanceof HTMLDetailsElement)) return null
  menu.open = false
  return menu.querySelector<HTMLElement>('summary')
}

function closeOpenConversationMenus(except?: HTMLDetailsElement | null) {
  assistantPage.value?.querySelectorAll<HTMLDetailsElement>('details.conversation-menu[open]').forEach((menu) => {
    if (menu !== except) menu.open = false
  })
}

function onConversationMenuSummaryClick(event: Event) {
  const menu = event.currentTarget instanceof HTMLElement ? event.currentTarget.closest('details.conversation-menu') : null
  if (menu instanceof HTMLDetailsElement) closeOpenConversationMenus(menu)
}

function onDocumentPointerDown(event: PointerEvent) {
  if (archiveTarget.value || deleteTarget.value) return
  const target = event.target
  const menu = target instanceof Element ? target.closest('details.conversation-menu') : null
  if (!menu) closeOpenConversationMenus()
}

function onAssistantPageKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  const target = event.target
  const menu = target instanceof Element ? target.closest<HTMLDetailsElement>('details.conversation-menu[open]') : null
  if (menu) {
    event.preventDefault()
    event.stopPropagation()
    menu.open = false
    menu.querySelector<HTMLElement>('summary')?.focus({ preventScroll: true })
    return
  }
  if (!mobileSidebarOpen.value) return
  event.preventDefault()
  event.stopPropagation()
  closeMobileSidebar()
}

async function send() {
  const content = draft.value
  if (!content.trim() || busy.value || !canSend.value || !store.selectedConversationId) return
  error.value = null; busy.value = true
  pendingUserEcho.value = { conversationId: store.selectedConversationId, content }
  const submittedConversationId = store.selectedConversationId
  draft.value = ''
  try {
    await store.sendMessage(submittedConversationId, { client_message_id: crypto.randomUUID(), content })
    pendingUserEcho.value = null
  } catch (reason) { pendingUserEcho.value = null; draft.value = content; error.value = reason instanceof Error ? reason.message : '消息发送失败' } finally { busy.value = false }
}

function composerKeydown(event: KeyboardEvent) {
  if (event.key !== 'Enter' || event.shiftKey || composing.value || event.isComposing || event.keyCode === 229) return
  event.preventDefault()
  void send()
}

function resizeComposer(event?: Event) {
  const input = (event?.currentTarget as HTMLTextAreaElement | null) ?? composerInput.value
  if (!input) return
  input.style.height = 'auto'
  const height = Math.min(input.scrollHeight, 160)
  input.style.height = `${height}px`
  input.style.overflowY = input.scrollHeight > 160 ? 'auto' : 'hidden'
}

function onMessageScroll() {
  const element = messageScroll.value
  if (!element) return
  atMessageBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 72
  showJumpLatest.value = !atMessageBottom.value
}

function jumpToLatest() {
  if (!messageScroll.value) return
  messageScroll.value.scrollTop = messageScroll.value.scrollHeight
  atMessageBottom.value = true
  showJumpLatest.value = false
}

async function loadConversationList(reset = true) {
  const request = ++conversationListRequest
  const generation = reset ? ++conversationListGeneration : conversationListGeneration
  const query = conversationQuery.value.trim()
  const archived = showArchived.value
  conversationSearchBusy.value = true
  if (reset) { conversationCursor.value = null; conversationHasMore.value = false }
  try {
    const result = await store.listConversations({ q: query, archived, cursor: reset ? undefined : conversationCursor.value ?? undefined, append: !reset })
    if (!result || request !== conversationListRequest || generation !== conversationListGeneration || query !== conversationQuery.value.trim() || archived !== showArchived.value) return
    conversationCursor.value = result?.page?.next_cursor ?? null
    conversationHasMore.value = Boolean(result?.page?.has_more)
  } catch (reason) {
    if (request === conversationListRequest && generation === conversationListGeneration && query === conversationQuery.value.trim() && archived === showArchived.value) error.value = reason instanceof Error ? reason.message : '会话列表读取失败'
  }
  finally { if (request === conversationListRequest) conversationSearchBusy.value = false }
}

function startRename(conversation: (typeof visibleConversations.value)[number], event?: Event) {
  if (conversation.status !== 'ACTIVE') return
  closeConversationMenu(event)
  renameId.value = conversation.id; renameDraft.value = conversation.title; mobileSidebarOpen.value = true
}

async function saveRename() {
  if (!renameId.value || busy.value) return
  busy.value = true; error.value = null
  try { await store.renameConversation(renameId.value, renameDraft.value); renameId.value = null }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '重命名失败' }
  finally { busy.value = false }
}

async function copyConversationId(id: string, event?: Event) {
  closeConversationMenu(event)
  try { await navigator.clipboard.writeText(id) } catch { error.value = '复制会话 ID 失败' }
}

async function startArchive(conversation: (typeof visibleConversations.value)[number], event?: MouseEvent) {
  if (conversation.status !== 'ACTIVE' || busy.value || archiveLoading.value || deleteTarget.value || confirmationStore.state === 'loading') return
  archiveTrigger.value = closeConversationMenu(event) ?? (event?.currentTarget instanceof HTMLElement ? event.currentTarget : null)
  archiveTarget.value = conversation; archiveLoading.value = true; archiveNotice.value = null; error.value = null
  try {
    const preview = await confirmationStore.loadPreview({ type: 'conversation', id: conversation.id }, 'resource.archive')
    if (preview.resource_ref.type !== 'conversation' || preview.resource_ref.id !== conversation.id || preview.action !== 'resource.archive') throw new Error('服务端确认预览与当前会话不匹配')
    await nextTick()
    archiveDialog.value?.querySelector<HTMLElement>('button')?.focus()
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '读取归档确认预览失败'
    const trigger = archiveTrigger.value
    archiveTarget.value = null
    confirmationStore.reset()
    await nextTick()
    trigger?.focus({ preventScroll: true })
  }
  finally { archiveLoading.value = false }
}

function cancelArchive() {
  if (archiveLoading.value || busy.value) return
  closeArchiveDialog()
}

function closeArchiveDialog() {
  const trigger = archiveTrigger.value
  archiveTarget.value = null
  archiveNotice.value = null
  confirmationStore.reset()
  void nextTick(() => trigger?.focus({ preventScroll: true }))
}

function trapArchiveFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !archiveDialog.value) return
  const focusable = [...archiveDialog.value.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')]
  if (!focusable.length) { event.preventDefault(); archiveDialog.value.focus(); return }
  const first = focusable[0]!, last = focusable.at(-1)!
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

async function confirmArchive() {
  const target = archiveTarget.value
  const preview = confirmationStore.preview
  if (!target || !preview || !preview.can_confirm || preview.resource_ref.type !== 'conversation' || preview.resource_ref.id !== target.id || preview.action !== 'resource.archive' || archiveLoading.value) return
  const previous = store.conversations
  const index = previous.findIndex((item) => item.id === target.id)
  const nextConversation = previous[index + 1] ?? previous[index - 1] ?? null
  archiveLoading.value = true; busy.value = true; error.value = null
  try {
    const confirmation = await confirmationStore.create()
    await store.archiveConversation(target.id, confirmation.id)
    closeArchiveDialog()
    if (store.selectedConversationId === null && nextConversation) await store.loadConversation(nextConversation.id)
    await nextTick()
    const focusTarget = [...document.querySelectorAll<HTMLElement>('.conversation-item')].find((element) => element.dataset.conversationId === (nextConversation?.id ?? '')) ?? conversationSearchInput.value
    focusTarget?.focus({ preventScroll: true })
  } catch (reason) {
    if (reason instanceof ApiClientError && (reason.status === 409 || reason.status === 412)) {
      archiveNotice.value = '会话状态已变化。已重新读取确认预览，请检查后再次确认。'
      try {
        confirmationStore.reset()
        const refreshed = await confirmationStore.loadPreview({ type: 'conversation', id: target.id }, 'resource.archive')
        if (refreshed.resource_ref.id !== target.id || refreshed.resource_ref.type !== 'conversation' || refreshed.action !== 'resource.archive') throw new Error('刷新后的确认预览与当前会话不匹配')
      } catch (refreshReason) { archiveNotice.value = refreshReason instanceof Error ? `确认预览刷新失败：${refreshReason.message}` : '确认预览刷新失败' }
    } else error.value = reason instanceof Error ? reason.message : '归档会话失败'
  }
  finally { archiveLoading.value = false; busy.value = false }
}

function isCurrentDeleteAttempt(id: string, projectId: string | null, generation: number) {
  const attempt = deleteAttempt.value
  return deleteRequestGeneration === generation && deleteTarget.value?.id === id && attempt?.targetId === id && attempt.projectId === projectId && store.projectId === projectId
}

function knownNonterminalRun(conversation: ContractConversation) {
  const run = store.run
  return run?.conversation_id === conversation.id && !TERMINAL_RUN_STATUSES.has(run.status)
}

async function loadDeletePreview(target: ContractConversation, projectId: string | null, generation: number) {
  deleteLoading.value = true
  await nextTick()
  if (!isCurrentDeleteAttempt(target.id, projectId, generation)) return
  deleteDialog.value?.focus({ preventScroll: true })
  try {
    const preview = await confirmationStore.loadPreview({ type: 'conversation', id: target.id }, 'conversation.delete')
    if (!isCurrentDeleteAttempt(target.id, projectId, generation)) return
    if (preview.resource_ref.type !== 'conversation' || preview.resource_ref.id !== target.id || preview.action !== 'conversation.delete') throw new Error('服务端确认预览与当前会话不匹配')
    if (!preview.etag) throw new Error('服务端删除预览缺少会话版本标识，无法安全删除。')
    if (deleteAttempt.value) deleteAttempt.value = { ...deleteAttempt.value, etag: preview.etag }
  } catch (reason) {
    if (!isCurrentDeleteAttempt(target.id, projectId, generation)) return
    if (reason instanceof ApiClientError && reason.code === 'CONVERSATION_BUSY') {
      deleteNotice.value = '该会话仍有关联运行。请关闭此窗口，打开该会话，停止回答并等待运行结束后再删除；不会自动取消业务任务。'
    } else deleteNotice.value = reason instanceof Error ? reason.message : '读取删除确认预览失败'
  } finally {
    if (isCurrentDeleteAttempt(target.id, projectId, generation)) {
      deleteLoading.value = false
      await nextTick()
      if (isCurrentDeleteAttempt(target.id, projectId, generation)) focusDeleteDialogCancel()
    }
  }
}

async function startDelete(conversation: (typeof visibleConversations.value)[number], event?: MouseEvent) {
  if (!['ACTIVE', 'ARCHIVED'].includes(conversation.status) || busy.value || archiveLoading.value || deleteLoading.value || archiveTarget.value || deleteTarget.value || confirmationStore.state === 'loading') return
  if (!store.projectId || !conversation.etag) {
    error.value = '会话缺少项目或版本标识，无法安全删除。请刷新列表后重试。'
    return
  }
  if (knownNonterminalRun(conversation)) {
    error.value = '该会话仍有未结束的回答。请先停止回答并等待状态变为终态后再删除；不会自动取消预测或业务任务。'
    return
  }
  const menu = event?.currentTarget instanceof HTMLElement ? event.currentTarget.closest('details.conversation-menu') : null
  if (menu instanceof HTMLDetailsElement) menu.open = false
  const visibleAtStart = [...visibleConversations.value]
  const targetIndex = visibleAtStart.findIndex((item) => item.id === conversation.id)
  const wasSelected = store.selectedConversationId === conversation.id
  const adjacentId = wasSelected && targetIndex >= 0 ? visibleAtStart[targetIndex + 1]?.id ?? visibleAtStart[targetIndex - 1]?.id ?? null : null
  deleteTrigger.value = menu?.querySelector<HTMLElement>('summary') ?? (event?.currentTarget instanceof HTMLElement ? event.currentTarget.closest('.conversation-entry')?.querySelector<HTMLElement>('.conversation-item') ?? null : null)
  deleteTarget.value = conversation
  deleteAttempt.value = { targetId: conversation.id, etag: conversation.etag, confirmationId: null, idempotencyKey: null, projectId: store.projectId, wasSelected, adjacentId }
  deleteNotice.value = null
  error.value = null
  confirmationStore.reset()
  const generation = ++deleteRequestGeneration
  await loadDeletePreview(conversation, store.projectId, generation)
}

async function retryDeletePreview() {
  const target = deleteTarget.value
  if (!target || deleteLoading.value || busy.value) return
  if (deleteAttempt.value) deleteAttempt.value = { ...deleteAttempt.value, confirmationId: null, idempotencyKey: null }
  confirmationStore.reset()
  const generation = deleteRequestGeneration
  await loadDeletePreview(target, deleteAttempt.value?.projectId ?? null, generation)
}

function discardDeleteDialog(restoreFocus = true) {
  const trigger = deleteTrigger.value
  deleteRequestGeneration += 1
  deleteTarget.value = null
  deleteNotice.value = null
  deleteAttempt.value = null
  deleteLoading.value = false
  confirmationStore.reset()
  if (restoreFocus) void nextTick(() => trigger?.focus({ preventScroll: true }))
}

function focusDeleteDialogCancel() {
  const dialog = deleteDialog.value
  const cancel = dialog?.querySelector<HTMLElement>('[data-delete-cancel]:not(:disabled)')
  ;(cancel ?? dialog)?.focus({ preventScroll: true })
}

function invalidateDeleteBusyOwner() {
  deleteBusyOwner += 1
  busy.value = false
  deleteLoading.value = false
}

function cancelDelete() {
  if (deleteLoading.value || busy.value) return
  discardDeleteDialog()
}

function trapDeleteFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !deleteDialog.value) return
  const focusable = [...deleteDialog.value.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')]
  if (!focusable.length) { event.preventDefault(); deleteDialog.value.focus(); return }
  const first = focusable[0]!, last = focusable.at(-1)!
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}

function deletionFailureMessage(reason: unknown) {
  if (reason instanceof ApiClientError && reason.code === 'CONVERSATION_BUSY') return '该会话仍有关联运行。请关闭此窗口，打开该会话，停止回答并等待运行结束后再删除；不会自动取消业务任务。'
  return reason instanceof Error ? reason.message : '删除聊天失败'
}

async function confirmDelete() {
  const target = deleteTarget.value
  const initialAttempt = deleteAttempt.value
  const preview = confirmationStore.preview
  const projectId = initialAttempt?.projectId ?? null
  const generation = deleteRequestGeneration
  if (!target || !initialAttempt || initialAttempt.targetId !== target.id || !projectId || !preview || !preview.can_confirm || preview.resource_ref.type !== 'conversation' || preview.resource_ref.id !== target.id || preview.action !== 'conversation.delete' || deleteLoading.value || busy.value) return
  if (knownNonterminalRun(target)) {
    deleteNotice.value = '该会话仍有未结束的回答。请关闭此窗口，停止回答并等待终态后再删除。'
    return
  }
  const deletingCurrent = initialAttempt.wasSelected && (store.selectedConversationId === target.id || store.selectedConversationId === null)
  const adjacentConversation = deletingCurrent && initialAttempt.adjacentId
    ? visibleConversations.value.find((item) => item.id === initialAttempt.adjacentId && item.project_id === projectId) ?? null
    : null
  deleteLoading.value = true
  busy.value = true
  const busyOwner = ++deleteBusyOwner
  error.value = null
  try {
    await nextTick()
    if (isCurrentDeleteAttempt(target.id, projectId, generation)) deleteDialog.value?.focus({ preventScroll: true })
    if (!deleteAttempt.value?.confirmationId) {
      const confirmation = await confirmationStore.create()
      if (!isCurrentDeleteAttempt(target.id, projectId, generation)) return
      deleteAttempt.value = { ...deleteAttempt.value!, confirmationId: confirmation.id }
    }
    if (!deleteAttempt.value?.idempotencyKey) deleteAttempt.value = { ...deleteAttempt.value!, idempotencyKey: createIdempotencyKey() }
    const attempt = deleteAttempt.value
    if (!attempt?.confirmationId || !attempt.idempotencyKey) throw new Error('删除确认状态已丢失；请重新读取确认预览。')
    const body: ContractConversationDelete = { confirmation_id: attempt.confirmationId }
    await store.deleteConversation(attempt.targetId, body, attempt.idempotencyKey, attempt.etag, attempt.projectId)
    if (!isCurrentDeleteAttempt(target.id, projectId, generation)) return

    if (deletingCurrent) {
      draft.value = ''
      pendingUserEcho.value = null
      renameId.value = null
      renameDraft.value = ''
      predictionLoading.value = {}
      clearProjectSummaries()
    }
    discardDeleteDialog(false)
    if (deletingCurrent && adjacentConversation) {
      try { await store.loadConversation(adjacentConversation.id) }
      catch (reason) { error.value = reason instanceof Error ? `聊天已删除，但相邻会话加载失败：${reason.message}` : '聊天已删除，但相邻会话加载失败' }
    }
    await nextTick()
    const focusTarget = deletingCurrent
      ? [...document.querySelectorAll<HTMLElement>('.conversation-item')].find((element) => element.dataset.conversationId === (adjacentConversation?.id ?? '')) ?? conversationSearchInput.value
      : [...document.querySelectorAll<HTMLElement>('.conversation-item')].find((element) => element.dataset.conversationId === target.id) ?? conversationSearchInput.value
    focusTarget?.focus({ preventScroll: true })
  } catch (reason) {
    if (!isCurrentDeleteAttempt(target.id, projectId, generation)) return
    if (reason instanceof ApiClientError && (reason.status === 412 || reason.code === 'ETAG_MISMATCH' || reason.code === 'CONFIRMATION_INVALIDATED' || reason.code === 'CONFIRMATION_INVALID' || reason.code === 'CONFIRMATION_EXPIRED')) {
      if (deleteAttempt.value) deleteAttempt.value = { ...deleteAttempt.value, confirmationId: null, idempotencyKey: null }
      confirmationStore.reset()
      deleteNotice.value = '会话状态或确认已变化。已刷新确认预览，请检查后再次确认。'
      await loadDeletePreview(target, projectId, generation)
    } else if (reason instanceof ApiClientError && reason.code === 'CONVERSATION_BUSY') {
      if (deleteAttempt.value) deleteAttempt.value = { ...deleteAttempt.value, confirmationId: null, idempotencyKey: null }
      confirmationStore.reset()
      deleteNotice.value = deletionFailureMessage(reason)
    } else {
      deleteNotice.value = `${deletionFailureMessage(reason)}。可重试；系统会复用本次确认与幂等键。`
    }
  } finally {
    if (deleteBusyOwner === busyOwner) {
      deleteLoading.value = false
      busy.value = false
      await nextTick()
      if (isCurrentDeleteAttempt(target.id, projectId, generation)) focusDeleteDialogCancel()
    }
  }
}

async function copyMessage(message: ContractMessage) {
  const markdown = (message.blocks ?? []).flatMap((block) => block.type === 'text' ? [block.text] : []).join('\n\n')
  try { await navigator.clipboard.writeText(markdown) } catch { error.value = '复制消息失败' }
}

async function copyProjectedMessage(blocks: string[]) {
  try { await navigator.clipboard.writeText(projectedTextBlocks(blocks).map((block) => block.text).join('\n\n')) }
  catch { error.value = '复制消息失败' }
}

async function stopAnswer() {
  if (!activeRun.value || !canStop.value || busy.value || archiveTarget.value || deleteTarget.value) return
  error.value = null; busy.value = true
  try { await store.cancelRun(activeRun.value.id, activeRun.value.etag, '用户在业务助手中停止回答') } catch (reason) { error.value = reason instanceof Error ? reason.message : '停止回答失败' } finally { busy.value = false }
}

async function confirmPending() {
  if (!activeRun.value || !pendingConfirmation.value || busy.value || archiveTarget.value || deleteTarget.value) return
  error.value = null; busy.value = true
  try { await store.confirmRun(activeRun.value.id, pendingConfirmation.value, activeRun.value.etag) } catch (reason) { error.value = reason instanceof Error ? reason.message : '确认操作失败' } finally { busy.value = false }
}

async function recoverRun() {
  if (!activeRun.value || busy.value || archiveTarget.value || deleteTarget.value) return
  error.value = null; busy.value = true
  try { await store.resumeRun(activeRun.value.id, makeResumeBody('recovery'), activeRun.value.etag) } catch (reason) { error.value = reason instanceof Error ? reason.message : '恢复运行失败' } finally { busy.value = false }
}

async function openPrediction(block: AssistantBlock) {
  if (block.type !== 'prediction_result') return
  const id = blockId(block)
  if (!id || store.predictionResults[id]) return
  await requeryPrediction(id)
}

async function requeryRegistrationDraft(id: string) {
  try { await store.getRegistrationDraft(id) } catch { /* the store retains the public request error for this reference */ }
}

async function requeryPrediction(id: string) {
  if (predictionLoading.value[id]) return
  const requestConversationId = store.selectedConversationId
  predictionLoading.value = { ...predictionLoading.value, [id]: true }
  error.value = null
  try { await store.getPredictionResult(id) } catch (reason) { if (store.selectedConversationId === requestConversationId) error.value = reason instanceof Error ? reason.message : '预测结果暂不可用' }
  finally { const next = { ...predictionLoading.value }; delete next[id]; predictionLoading.value = next }
}

onMounted(() => {
  summaryViewMounted = true
  document.addEventListener('pointerdown', onDocumentPointerDown)
  void refresh()
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  if (conversationSearchTimer) clearTimeout(conversationSearchTimer)
  summaryViewMounted = false
  summaryCapabilityRequest += 1
  clearProjectSummaries()
  store.stopEventStream()
})

watch([conversationQuery, showArchived], () => {
  if (conversationSearchTimer) clearTimeout(conversationSearchTimer)
  conversationSearchTimer = setTimeout(() => void loadConversationList(), 250)
})
watch(() => [store.messages.length, store.streamTextProjections.map((item) => item.text).join(''), projectedOnlyMessages.value.length] as const, async () => {
  await nextTick()
  if (atMessageBottom.value) jumpToLatest()
  else showJumpLatest.value = true
})
watch(draft, () => void nextTick(() => resizeComposer()))
</script>

<template>
  <div ref="assistantPage" class="assistant-page" :class="{ 'sidebar-collapsed': sidebarCollapsed, 'mobile-sidebar-open': mobileSidebarOpen }" @keydown="onAssistantPageKeydown">
    <button v-if="mobileSidebarOpen" class="sidebar-scrim" aria-label="关闭会话列表" @click="closeMobileSidebar"></button>
    <header class="assistant-header">
      <div class="assistant-brand"><button class="icon-button platform-navigation-trigger" data-platform-navigation-trigger aria-label="切换平台导航" aria-controls="business-navigation" :aria-expanded="app.mobileNavigationOpen || !app.navigationCollapsed" @click="app.toggleNavigation()">☰</button><button ref="mobileMenuTrigger" class="icon-button mobile-menu-button" aria-label="打开会话列表" aria-controls="assistant-conversation-list" :aria-expanded="mobileSidebarOpen" @click="mobileSidebarOpen = true">☰</button><span class="brand-mark">✦</span><strong>色谱智能助手</strong></div>
      <div class="header-actions">
        <button class="icon-button desktop-menu-button" aria-label="折叠会话列表" @click="sidebarCollapsed = !sidebarCollapsed">{{ sidebarCollapsed ? '☰' : '‹' }}</button>
        <span class="context-chip" :class="{ blocked: !store.projectId }">{{ store.projectId ? '工作区已就绪' : '等待访问上下文' }}</span>
        <button class="icon-button" :disabled="refreshing" aria-label="刷新恢复" title="刷新恢复" @click="refresh">{{ refreshing ? '…' : '↻' }}</button>
        <button class="button primary" :disabled="busy || Boolean(deleteTarget) || !store.projectId" @click="newConversation">新建会话</button>
      </div>
    </header>

    <p v-if="error || store.error" class="error-banner" role="alert">{{ error || store.error }}</p>

    <section v-if="!store.projectId && store.state !== 'loading'" class="blocking-card">
      <strong>服务端能力阻塞</strong>
      <p>当前访问上下文没有可用项目，助手无法创建业务会话。请先由服务端提供项目访问范围。</p>
    </section>

    <div class="assistant-shell">
      <aside id="assistant-conversation-list" class="conversation-panel" aria-label="会话列表" :aria-hidden="sidebarCollapsed">
        <div class="sidebar-heading"><div class="panel-title"><span>{{ showArchived ? '已归档' : '会话' }}</span><span class="count">{{ visibleConversations.length }}</span></div><button class="button primary small new-conversation-button" :disabled="busy || Boolean(deleteTarget) || !store.projectId" @click="newConversation">＋ 新建</button></div>
        <label class="conversation-search"><span>⌕</span><input ref="conversationSearchInput" v-model="conversationQuery" type="search" placeholder="搜索会话" aria-label="搜索会话" /></label>
        <div class="conversation-tabs"><button :class="{ selected: !showArchived }" @click="showArchived = false">最近</button><button :class="{ selected: showArchived }" @click="showArchived = true">已归档</button></div>
        <div v-if="store.state === 'loading'" class="empty-small">正在恢复会话…</div>
        <div v-else-if="!visibleConversations.length" class="empty-small">{{ conversationSearchBusy ? '正在搜索…' : showArchived ? '没有已归档会话' : conversationQuery ? '没有匹配的会话' : '还没有业务会话' }}</div>
        <div v-for="([group, conversations]) in conversationGroups" :key="group" class="conversation-group"><h3>{{ group }}</h3>
          <div v-for="conversation in conversations" :key="conversation.id" class="conversation-entry" :class="{ selected: conversation.id === store.selectedConversationId }">
            <button class="conversation-item" :data-conversation-id="conversation.id" :aria-current="conversation.id === store.selectedConversationId ? 'page' : undefined" @click="chooseConversation(conversation.id)">
              <span class="conversation-dot" :class="conversation.status.toLowerCase()"></span>
              <span class="conversation-copy"><strong>{{ conversation.title || '未命名会话' }}</strong><small>{{ formatTime(conversation.last_message_at || conversation.updated_at) }}</small></span>
              <span v-if="hasLoadedActiveRun(conversation)" class="run-mark" title="有进行中的业务回答">●</span>
            </button>
            <details class="conversation-menu"><summary aria-label="会话操作" title="会话操作" @click="onConversationMenuSummaryClick">···</summary><div class="menu-popover">
              <button v-if="conversation.status === 'ACTIVE'" @click="startRename(conversation, $event)">重命名</button>
              <button @click="copyConversationId(conversation.id, $event)">复制会话 ID</button>
              <button v-if="conversation.status === 'ACTIVE'" :disabled="busy || Boolean(archiveTarget)" @click="startArchive(conversation, $event)">移出列表</button>
              <button v-if="conversation.status === 'ACTIVE' || conversation.status === 'ARCHIVED'" :disabled="busy || archiveLoading || deleteLoading || Boolean(archiveTarget) || Boolean(deleteTarget)" @click="startDelete(conversation, $event)">删除聊天…</button>
            </div></details>
            <form v-if="renameId === conversation.id" class="rename-form" @submit.prevent="saveRename"><input v-model="renameDraft" maxlength="120" aria-label="会话名称" autofocus /><button class="button primary small" :disabled="busy">保存</button><button type="button" class="button secondary small" @click="renameId = null">取消</button></form>
          </div>
        </div>
        <button v-if="conversationHasMore" class="load-more-button" :disabled="conversationSearchBusy" @click="loadConversationList(false)">{{ conversationSearchBusy ? '加载中…' : '加载更多' }}</button>
      </aside>

      <main class="chat-panel">
        <div v-if="selectedConversation" class="chat-heading">
          <button class="icon-button chat-sidebar-toggle" aria-label="显示会话列表" @click="sidebarCollapsed = false; mobileSidebarOpen = true">☰</button>
          <div class="chat-title"><strong>{{ selectedConversation.title || '助手会话' }}</strong><span>{{ selectedConversation.status === 'ARCHIVED' ? '已归档 · 只读' : conversationModeLabel }}</span></div>
          <span v-if="activeRun" class="status-pill" :class="activeRun.status.toLowerCase()">{{ statusLabel(activeRun.status) }}</span>
        </div>
        <div v-else class="chat-heading"><button class="icon-button chat-sidebar-toggle" aria-label="显示会话列表" @click="sidebarCollapsed = false; mobileSidebarOpen = true">☰</button><div class="chat-title"><strong>新的对话</strong><span>色谱智能助手</span></div></div>

        <div ref="messageScroll" class="message-scroll" aria-live="polite" @scroll.passive="onMessageScroll">
          <div v-if="!selectedConversation" class="welcome-state"><div class="welcome-icon">✦</div><h2>今天想处理什么？</h2><p>围绕当前项目询问预测、数据摘要或模型信息。</p><div class="prompt-examples"><button @click="draft = '请根据这个项目的输入结构发起一次预测，并说明所选模型和关键结果。'">预测</button><button @click="draft = '请总结这个项目最近的预测记录和数据版本。'">数据摘要</button><button @click="draft = '请查询当前可用模型，并说明如何创建模型登记草稿。'">模型草稿</button></div><button class="button primary" :disabled="busy || Boolean(deleteTarget) || !store.projectId" @click="newConversation">开始新对话</button></div>
          <div v-else-if="!store.messages.length && !pendingEchoIsVisible" class="welcome-state"><div class="welcome-icon">✦</div><h2>准备好了</h2><p>输入你的色谱业务问题，助手会在当前项目允许的范围内处理。</p></div>
          <article v-if="pendingEchoIsVisible && pendingUserEcho" class="message-row user pending-message"><div class="avatar">我</div><div class="message-body"><div class="message-meta"><strong>你</strong><span>正在发送…</span></div><div class="message-bubble user-bubble user-message-text">{{ pendingUserEcho.content }}</div></div></article>
          <article v-for="message in store.messages" :key="message.id" class="message-row" :class="message.role">
            <div class="avatar">{{ message.role === 'user' ? '我' : '✦' }}</div>
            <div class="message-body">
              <div class="message-meta"><strong>{{ message.role === 'user' ? '你' : '色谱助手' }}</strong><time>{{ formatTime(message.created_at) }}</time><button class="message-action" title="复制消息" aria-label="复制消息" @click="copyMessage(message)">复制</button></div>
              <div v-if="message.role === 'user'" class="message-bubble user-bubble user-message-text">{{ (message.blocks ?? []).filter((block) => block.type === 'text').map((block) => block.type === 'text' ? block.text : '').join('\n') }}</div>
              <div v-else class="message-bubble assistant-bubble">
                <template v-if="message.blocks?.length">
                  <div v-for="(block, index) in message.blocks" :key="`${message.id}-${index}`" class="message-block">
                    <AssistantText v-if="block.type === 'text'" :text="displayedBlockText(message.id, index, block)" />
                    <div v-else-if="block.type === 'prediction_result'" class="result-card prediction-card">
                      <div><span class="card-kicker">预测结果</span><strong>{{ block.title }}</strong></div>
                      <p v-if="predictionDetail(blockId(block))">状态：{{ predictionDetail(blockId(block))?.status }} · 类型：{{ predictionDetail(blockId(block))?.chromatographyType }}</p>
                      <p v-else>结果已由服务端生成，可按需读取详细结果。</p>
                      <button class="text-button" :disabled="!blockId(block) || Boolean(store.predictionResults[blockId(block)]) || predictionLoading[blockId(block)]" @click="openPrediction(block)">{{ predictionLoading[blockId(block)] ? '读取中…' : store.predictionResults[blockId(block)] ? '结果已读取' : '读取结果' }}</button>
                      <template v-if="predictionDetail(blockId(block))">
                        <p v-if="predictionDetail(blockId(block))?.errorMessage" class="prediction-detail-error" role="alert">{{ predictionDetail(blockId(block))?.errorCode ? `${predictionDetail(blockId(block))?.errorCode}：` : '' }}{{ predictionDetail(blockId(block))?.errorMessage }}</p>
                        <template v-if="predictionDetail(blockId(block))?.kind === 'tlc'">
                          <dl class="prediction-detail-fields"><div><dt>模型 ID</dt><dd>{{ predictionDetail(blockId(block))?.modelId }}</dd></div><div><dt>模型版本</dt><dd>{{ predictionDetail(blockId(block))?.modelVersion }}</dd></div><div><dt>模型制品 SHA-256</dt><dd>{{ predictionDetail(blockId(block))?.artifactSha256 }}</dd></div></dl>
                          <ul class="prediction-rf-list"><li v-for="(rf, index) in predictionDetail(blockId(block))?.rfValues" :key="`${blockId(block)}-rf-${index}`">{{ predictionDetail(blockId(block))?.rfValues.length === 1 ? '预测 Rf' : `预测 Rf ${index + 1}` }}：{{ rf }}</li></ul>
                          <ul v-if="predictionDetail(blockId(block))?.warnings.length" class="prediction-warning-list"><li v-for="warning in predictionDetail(blockId(block))?.warnings" :key="warning">{{ warning }}</li></ul>
                        </template>
                        <p v-else-if="predictionDetail(blockId(block))?.kind === 'pending'" class="muted">预测尚未成功完成；当前不显示预测数值。</p>
                        <p v-else-if="predictionDetail(blockId(block))?.kind === 'other'" class="muted">已读取非 TLC 预测结果；本卡不显示 TLC 数值。</p>
                      </template>
                    </div>
                    <div v-else-if="block.type === 'confirmation_request'" class="result-card confirmation-card">
                      <div><span class="card-kicker">需要确认</span><strong>{{ block.action }}</strong></div>
                      <p>该业务动作需要服务端确认绑定后继续，确认只会推进当前助手运行。</p>
                      <p v-if="store.confirmationNotice && store.confirmationNoticeRunId === activeRun?.id" class="success-note">{{ store.confirmationNotice }}</p>
                      <span v-else-if="pendingConfirmationBlocked" class="blocked-note">服务端返回多个待确认动作，请先刷新以核对绑定；当前没有动作可确认。</span>
                      <button v-else-if="pendingConfirmation" class="button primary small" :disabled="busy || Boolean(archiveTarget)" @click="confirmPending">验证确认并继续</button>
                      <span v-else class="blocked-note">确认绑定尚未由服务端提供，当前操作被阻塞。</span>
                    </div>
                    <div v-else-if="block.type === 'warning'" class="warning-card">{{ block.message }}</div>
                    <div v-else-if="block.type === 'job'" class="result-card"><span class="card-kicker">业务任务</span><strong>{{ block.title }}</strong><p>任务已提交，进度会在本会话中更新。</p></div>
                    <div v-else-if="block.type === 'artifact'" class="result-card"><span class="card-kicker">业务制品</span><strong>{{ block.title }}</strong><p>服务端已返回受支持制品引用。</p></div>
                  </div>
                </template>
                <span v-else class="muted">助手正在处理，请查看下方进度。</span>
                <AssistantText v-for="block in additionalProjectedBlocks(message)" :key="`${message.id}-projection-${block.index}`" :text="block.text" />
                <small v-if="message.status !== 'FINAL' || messageHasIncompleteProjection(message.id)" class="muted">{{ incompleteAnswerMessage }}</small>
              </div>
            </div>
          </article>
          <article v-for="message in projectedOnlyMessages" :key="`projection-${message.id}`" class="message-row assistant">
            <div class="avatar">✦</div>
            <div class="message-body">
              <div class="message-meta"><strong>色谱助手</strong><span>实时文本</span><button class="message-action" title="复制消息" aria-label="复制消息" @click="copyProjectedMessage(message.blocks)">复制</button></div>
              <div class="message-bubble assistant-bubble">
                <AssistantText v-for="block in projectedTextBlocks(message.blocks)" :key="`${message.id}-${block.index}`" :text="block.text" />
                <small v-if="message.incomplete" class="muted">{{ incompleteAnswerMessage }}</small>
              </div>
            </div>
          </article>
        </div>

        <details v-if="activeRun || visibleToolCalls.length || store.liveEvents.length" class="progress-panel">
          <summary class="progress-heading"><strong>{{ activeRun && !['SUCCEEDED', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'].includes(activeRun.status) ? '正在处理' : '运行详情' }}</strong><span v-if="activeRun">{{ statusLabel(activeRun.status) }}</span></summary>
          <div v-if="visibleToolCalls.length" class="tool-list">
            <div v-for="call in visibleToolCalls" :key="call.id" class="tool-row"><span class="tool-state" :class="call.status.toLowerCase()"></span><span>{{ toolLabel(call.tool_name) }}</span><span class="tool-status">{{ statusLabel(call.status) }}</span></div>
          </div>
          <article v-for="reference in predictionReferences" :key="reference.id" class="tool-result-card prediction-tool-result-card">
            <div class="draft-card-heading"><span class="card-kicker">预测资源回查</span><button class="text-button" :disabled="predictionLoading[reference.id]" @click="requeryPrediction(reference.id)">{{ predictionLoading[reference.id] ? '查询中…' : predictionDetail(reference.id) ? '重新查询' : '查询预测结果' }}</button></div>
            <p v-if="!predictionDetail(reference.id)" class="muted">服务端已创建预测任务。查询完成前不显示预测数值。</p>
            <template v-else>
              <p>状态：{{ predictionDetail(reference.id)?.status }} · 类型：{{ predictionDetail(reference.id)?.chromatographyType }}</p>
              <p v-if="predictionDetail(reference.id)?.errorMessage" class="prediction-detail-error" role="alert">{{ predictionDetail(reference.id)?.errorCode ? `${predictionDetail(reference.id)?.errorCode}：` : '' }}{{ predictionDetail(reference.id)?.errorMessage }}</p>
              <template v-if="predictionDetail(reference.id)?.kind === 'tlc'">
                <dl class="prediction-detail-fields"><div><dt>模型 ID</dt><dd>{{ predictionDetail(reference.id)?.modelId }}</dd></div><div><dt>模型版本</dt><dd>{{ predictionDetail(reference.id)?.modelVersion }}</dd></div><div><dt>模型制品 SHA-256</dt><dd>{{ predictionDetail(reference.id)?.artifactSha256 }}</dd></div></dl>
                <ul class="prediction-rf-list"><li v-for="(rf, index) in predictionDetail(reference.id)?.rfValues" :key="`${reference.id}-rf-${index}`">{{ predictionDetail(reference.id)?.rfValues.length === 1 ? '预测 Rf' : `预测 Rf ${index + 1}` }}：{{ rf }}</li></ul>
                <ul v-if="predictionDetail(reference.id)?.warnings.length" class="prediction-warning-list"><li v-for="warning in predictionDetail(reference.id)?.warnings" :key="warning">{{ warning }}</li></ul>
              </template>
              <p v-else-if="predictionDetail(reference.id)?.kind === 'pending'" class="muted">预测尚未成功完成；当前不显示预测数值。</p>
              <p v-else-if="predictionDetail(reference.id)?.kind === 'other'" class="muted">已读取非 TLC 预测结果；本卡不显示 TLC 数值。</p>
            </template>
          </article>
          <div v-for="call in summaryToolCalls" :key="`summary-${call.id}`" class="tool-result-card" :class="assistantToolGroup(call.tool_name)">
            <span class="card-kicker">{{ isExecutionMetadata(call) ? '工具执行元数据' : assistantToolGroup(call.tool_name) === 'data' ? '数据摘要' : assistantToolGroup(call.tool_name) === 'model' ? '模型草稿 / 查询' : assistantToolGroup(call.tool_name) === 'structure' ? '结构服务' : '预测结果' }}</span>
            <strong>{{ toolLabel(call.tool_name) }}</strong>
            <p v-if="isExecutionMetadata(call)" class="muted">服务端仅保留工具执行元数据，不包含可展示的业务结果明细。</p>
            <template v-else-if="assistantToolGroup(call.tool_name) === 'structure' && structureSummary(call)">
              <dl class="structure-summary"><div v-if="structureSummary(call)?.structureRef"><dt>结构引用</dt><dd>{{ structureSummary(call)?.structureRef }}</dd></div><div v-if="structureSummary(call)?.canonicalSmiles"><dt>规范化 SMILES</dt><dd>{{ structureSummary(call)?.canonicalSmiles }}</dd></div><div v-if="structureSummary(call)?.stereochemistryStatus"><dt>立体化学</dt><dd>{{ structureSummary(call)?.stereochemistryStatus }}</dd></div><div v-if="structureSummary(call)?.hasStereochemistry !== null"><dt>含立体信息</dt><dd>{{ structureSummary(call)?.hasStereochemistry ? '是' : '否' }}</dd></div></dl>
              <ul v-if="structureSummary(call)?.warnings.length" class="structure-warnings"><li v-for="warning in structureSummary(call)?.warnings" :key="warning">{{ warning }}</li></ul>
              <p v-if="!structureSummary(call)?.structureRef && !structureSummary(call)?.canonicalSmiles && !structureSummary(call)?.stereochemistryStatus && structureSummary(call)?.hasStereochemistry === null && !structureSummary(call)?.warnings.length" class="muted">服务端已返回结构结果，当前摘要没有可展示的公开字段。</p>
            </template>
            <template v-else-if="!isExecutionMetadata(call) && summaryEntries(call).length"><dl><div v-for="([key, value]) in summaryEntries(call)" :key="key" class="wrap-result-field"><dt>{{ key }}</dt><dd>{{ summaryValue(value) }}</dd></div></dl></template>
            <p v-else-if="!isExecutionMetadata(call)" class="muted">服务端已返回结果，当前摘要没有可展示的统计字段。</p>
          </div>
          <article v-for="reference in registrationDraftReferences" :key="reference.id" class="tool-result-card model registration-draft-card">
            <div class="draft-card-heading"><span class="card-kicker">登记草稿回查</span><button class="text-button" :disabled="store.registrationDraftStatus[reference.id] === 'loading'" @click="requeryRegistrationDraft(reference.id)">{{ store.registrationDraftStatus[reference.id] === 'loading' ? '回查中…' : store.registrationDraftStatus[reference.id] === 'ready' ? '重新回查' : '回查草稿' }}</button></div>
            <p v-if="store.registrationDraftStatus[reference.id] === 'error'" class="draft-query-error" role="alert">{{ store.registrationDraftErrors[reference.id] }}</p>
            <dl v-else-if="store.registrationDrafts[reference.id]">
              <div><dt>名称</dt><dd>{{ store.registrationDrafts[reference.id].name }}</dd></div>
              <div><dt>任务类型</dt><dd>{{ store.registrationDrafts[reference.id].task_type }}</dd></div>
              <div><dt>草稿状态</dt><dd>{{ store.registrationDrafts[reference.id].status }}</dd></div>
              <div><dt>版本</dt><dd>{{ store.registrationDrafts[reference.id].revision }}</dd></div>
              <div><dt>更新时间</dt><dd>{{ formatTime(store.registrationDrafts[reference.id].updated_at) }}</dd></div>
            </dl>
            <p v-else class="muted">服务端已返回登记草稿引用；回查后显示当前项目中的真实记录。</p>
          </article>
          <div v-for="event in store.liveEvents.slice(-3)" :key="event.seq" class="event-row"><span>{{ event.type }}</span><span>{{ event.data.message || event.data.stage || '服务端已更新进度' }}</span></div>
          <div v-if="failureNotice" class="failure-notice" role="alert"><span class="card-kicker">服务端错误</span><strong>{{ failureNotice.code }}</strong><p>{{ failureNotice.summary }}</p></div>
          <div v-if="activeRun?.status === 'WAITING_CONFIRMATION'" class="recovery-actions"><span v-if="store.confirmationNotice && store.confirmationNoticeRunId === activeRun?.id" class="success-note">{{ store.confirmationNotice }}</span><span v-else-if="pendingConfirmationBlocked" class="blocked-note">多个待确认动作无法安全选择；当前没有动作可确认。</span><button v-else-if="pendingConfirmation" class="button primary small" :disabled="busy || Boolean(archiveTarget)" @click="confirmPending">验证确认并继续</button><button v-else class="button secondary small" disabled>等待服务端确认绑定</button></div>
          <div v-else-if="activeRun?.status === 'INTERRUPTED'" class="recovery-actions"><button class="button secondary small" :disabled="busy" @click="recoverRun">恢复运行</button></div>
        </details>

        <div class="composer">
          <button v-if="showJumpLatest" class="jump-latest" @click="jumpToLatest">回到最新 ↓</button>
          <textarea ref="composerInput" v-model="draft" :disabled="busy || !selectedConversation || selectedConversation.status !== 'ACTIVE'" rows="1" :placeholder="selectedConversation?.status === 'ARCHIVED' ? '已归档会话为只读' : '给色谱助手发送消息…'" @input="resizeComposer" @keydown="composerKeydown" @compositionstart="composing = true" @compositionend="composing = false"></textarea>
          <div class="composer-footer"><span>{{ selectedConversation?.status === 'ARCHIVED' ? '归档会话只读' : `${conversationModeLabel} · Enter 发送，Shift+Enter 换行` }}</span><div><button v-if="canStop" class="button secondary" :disabled="busy" @click="stopAnswer">停止回答</button><button class="button primary" :disabled="busy || !canSend" @click="send">{{ busy ? '处理中…' : '发送' }}</button></div></div>
        </div>
      </main>

      <details class="insight-details"><summary>能力、项目摘要与运行信息</summary><aside class="insight-panel" aria-label="业务结果与能力">
        <section class="capability-panel" aria-label="服务端工具能力">
          <div class="capability-heading"><strong>当前构建能力</strong><span>服务端返回</span></div>
          <p v-if="store.priorityToolsState === 'loading'" class="muted" role="status">正在读取服务端工具目录…</p>
          <p v-else-if="store.priorityToolsState === 'error'" class="capability-error" role="alert">工具能力目录不可用：{{ store.priorityToolsError }}</p>
          <p v-else-if="store.priorityToolsState === 'idle'" class="muted" role="status">尚未读取服务端工具目录。</p>
          <p v-else-if="!firstRoundTools.length" class="muted" role="status">服务端目录未返回首轮预测、数据摘要或模型草稿能力。</p>
          <ul v-else class="capability-list"><li v-for="tool in firstRoundTools" :key="tool.name"><span>{{ toolLabel(tool.name) }}</span><small>{{ tool.requires_confirmation ? '需人工确认' : '无需单独确认' }}</small></li></ul>
        </section>
        <div class="side-section"><div class="panel-title"><span>业务结果</span></div>
          <div v-if="hasPredictionResult" class="side-card"><strong>预测结果</strong><p>结果卡片已附在消息中，可读取服务端详情。</p></div>
          <div v-else class="side-card muted-card"><strong>预测结果</strong><p>完成一次预测后，真实模型结果会显示在会话中。</p></div>
          <div v-if="hasDataSummary" class="side-card"><strong>数据摘要</strong><p>助手已返回项目数据版本与预测记录摘要。</p></div>
          <div v-else class="side-card muted-card"><strong>数据摘要</strong><p>当前会话尚无摘要结果；可用摘要能力以服务端工具目录为准。</p></div>
          <div v-if="hasModelResult" class="side-card"><strong>模型登记草稿 / 查询</strong><p>助手已返回服务端模型卡或登记草稿结果。</p></div>
          <div v-else class="side-card muted-card"><strong>模型卡结果</strong><p>当前会话尚无模型卡摘要；登记草稿详情会按服务端稳定资源引用单独回查。</p></div>
        </div>
        <section class="side-section summary-query-section" aria-label="项目数据统计">
          <div class="panel-title"><span>项目数据统计</span><button class="text-button" :disabled="!canQueryProjectSummary" @click="loadSummaryCapabilities(true)">刷新筛选项</button></div>
          <p v-if="!canQueryProjectSummary" class="muted">当前项目访问范围未解析，暂不能查询项目摘要。</p>
          <template v-else>
            <article class="summary-query-card">
              <strong>数据版本摘要</strong>
              <p v-if="domainCapabilityState === 'loading'" class="muted">正在读取数据域目录…</p>
              <p v-else-if="domainCapabilityState === 'error'" class="summary-query-error" role="alert">{{ domainCapabilityError }} <button class="text-button" @click="loadSummaryCapabilities(true)">重试</button></p>
              <p v-else-if="!dataDomains.length" class="muted">当前服务端未返回数据域；没有可选范围，因此未查询数据版本摘要。</p>
              <template v-else>
                <label class="summary-filter">数据域
                  <select v-model="selectedSummaryDomain" :disabled="domainCapabilityState !== 'ready'">
                    <option value="">请选择数据域</option>
                    <option v-for="domain in dataDomains" :key="domain.id" :value="domain.id">{{ domain.id }} · {{ domain.chromatography_type.toUpperCase() }} · v{{ domain.version }}</option>
                  </select>
                </label>
                <button class="button secondary small" :disabled="!canQueryProjectSummary || domainCapabilityState !== 'ready' || !dataDomains.some((domain) => domain.id === selectedSummaryDomain) || dataVersionSummaryState === 'loading'" @click="queryDataVersionSummary">{{ dataVersionSummaryState === 'loading' ? '查询中…' : '查询数据版本摘要' }}</button>
                <p v-if="dataVersionSummaryError" class="summary-query-error" role="alert">{{ dataVersionSummaryError }}</p>
                <div v-if="dataVersionSummary" class="summary-result" data-testid="data-version-summary">
                  <p><strong>本次摘要条目数：{{ dataVersionSummary.count }}</strong><span>接口最多汇总200条，不代表全量条数。</span></p>
                  <p>来源：{{ dataVersionSummary.source.source_type }}<template v-if="dataVersionSummary.source.version"> · 版本 {{ dataVersionSummary.source.version }}</template></p>
                  <dl v-if="summaryNumericEntries(dataVersionSummary).length" class="summary-stat-list"><div v-for="([field, stat]) in summaryNumericEntries(dataVersionSummary)" :key="field"><dt>{{ field }}</dt><dd>n={{ stat.count }} · 最小 {{ formatSummaryNumber(stat.minimum) }} · 平均 {{ formatSummaryNumber(stat.mean) }} · 最大 {{ formatSummaryNumber(stat.maximum) }}<template v-if="summaryStatUnit(stat.unit)"> · {{ summaryStatUnit(stat.unit) }}</template></dd></div></dl>
                  <p v-else class="muted">当前摘要没有可展示的数值统计。</p>
                  <p v-if="summaryCountEntries(dataVersionSummary, 'status_counts').length">状态：<span v-for="([name, count], index) in summaryCountEntries(dataVersionSummary, 'status_counts')" :key="name">{{ index ? ' · ' : '' }}{{ name }} {{ count }}</span></p>
                  <p v-if="summaryCountEntries(dataVersionSummary, 'missing').length">缺失字段：<span v-for="([name, count], index) in summaryCountEntries(dataVersionSummary, 'missing')" :key="name">{{ index ? ' · ' : '' }}{{ name }} {{ count }}</span></p>
                  <p v-if="summaryUnitEntries(dataVersionSummary).length">单位：<span v-for="([name, unit], index) in summaryUnitEntries(dataVersionSummary)" :key="name">{{ index ? ' · ' : '' }}{{ name }} {{ unit }}</span></p>
                  <div v-if="summarySourceIds(dataVersionSummary).length" class="summary-source-ids"><span>来源引用：</span><code v-for="id in summarySourceIds(dataVersionSummary)" :key="id">{{ id }}</code><small v-if="summarySourceIdsOmitted(dataVersionSummary)">另有 {{ summarySourceIdsOmitted(dataVersionSummary) }} 条未显示</small></div>
                </div>
              </template>
            </article>
            <article class="summary-query-card">
              <strong>预测记录摘要</strong>
              <label class="summary-filter">色谱类型
                <select v-model="selectedSummaryType">
                  <option value="">全部服务端类型</option>
                  <option v-for="type in predictionTypes" :key="type.id" :value="type.id">{{ type.display_name }}（{{ type.id }}）</option>
                </select>
              </label>
              <p v-if="typeCapabilityState === 'loading'" class="muted">正在读取色谱类型目录…</p>
              <p v-else-if="typeCapabilityState === 'error'" class="summary-query-error" role="alert">{{ typeCapabilityError }}；仍可查询全部类型摘要。</p>
              <p v-else-if="!predictionTypes.length" class="muted">能力目录未返回具体类型；可查询全部类型摘要。</p>
              <button class="button secondary small" :disabled="!canQueryProjectSummary || Boolean(selectedSummaryType && (typeCapabilityState !== 'ready' || !predictionTypes.some((type) => type.id === selectedSummaryType))) || predictionRecordSummaryState === 'loading'" @click="queryPredictionRecordSummary">{{ predictionRecordSummaryState === 'loading' ? '查询中…' : selectedSummaryType ? '查询所选类型摘要' : '查询全部类型摘要' }}</button>
              <p v-if="predictionRecordSummaryError" class="summary-query-error" role="alert">{{ predictionRecordSummaryError }}</p>
              <div v-if="predictionRecordSummary" class="summary-result" data-testid="prediction-record-summary">
                <p><strong>本次摘要条目数：{{ predictionRecordSummary.count }}</strong><span>接口最多汇总200条，不代表全量条数。</span></p>
                <p>来源：{{ predictionRecordSummary.source.source_type }}<template v-if="predictionRecordSummary.chromatography_type"> · 类型 {{ predictionRecordSummary.chromatography_type.toUpperCase() }}</template><template v-if="predictionRecordSummary.source.version"> · 版本 {{ predictionRecordSummary.source.version }}</template></p>
                <dl v-if="summaryNumericEntries(predictionRecordSummary).length" class="summary-stat-list"><div v-for="([field, stat]) in summaryNumericEntries(predictionRecordSummary)" :key="field"><dt>{{ field }}</dt><dd>n={{ stat.count }} · 最小 {{ formatSummaryNumber(stat.minimum) }} · 平均 {{ formatSummaryNumber(stat.mean) }} · 最大 {{ formatSummaryNumber(stat.maximum) }}<template v-if="summaryStatUnit(stat.unit)"> · {{ summaryStatUnit(stat.unit) }}</template></dd></div></dl>
                <p v-else class="muted">当前摘要没有可展示的数值统计。</p>
                <p v-if="summaryCountEntries(predictionRecordSummary, 'status_counts').length">状态：<span v-for="([name, count], index) in summaryCountEntries(predictionRecordSummary, 'status_counts')" :key="name">{{ index ? ' · ' : '' }}{{ name }} {{ count }}</span></p>
                <p v-if="summaryCountEntries(predictionRecordSummary, 'missing').length">缺失字段：<span v-for="([name, count], index) in summaryCountEntries(predictionRecordSummary, 'missing')" :key="name">{{ index ? ' · ' : '' }}{{ name }} {{ count }}</span></p>
                <p v-if="summaryUnitEntries(predictionRecordSummary).length">单位：<span v-for="([name, unit], index) in summaryUnitEntries(predictionRecordSummary)" :key="name">{{ index ? ' · ' : '' }}{{ name }} {{ unit }}</span></p>
                <div v-if="summarySourceIds(predictionRecordSummary).length" class="summary-source-ids"><span>来源引用：</span><code v-for="id in summarySourceIds(predictionRecordSummary)" :key="id">{{ id }}</code><small v-if="summarySourceIdsOmitted(predictionRecordSummary)">另有 {{ summarySourceIdsOmitted(predictionRecordSummary) }} 条未显示</small></div>
              </div>
            </article>
          </template>
        </section>
      </aside></details>
    </div>
    <div v-if="archiveTarget" class="modal-scrim" @click.self="cancelArchive">
      <section ref="archiveDialog" class="archive-dialog" role="dialog" aria-modal="true" aria-labelledby="archive-title" tabindex="-1" @keydown.esc.stop.prevent="cancelArchive" @keydown="trapArchiveFocus">
        <button class="dialog-close" aria-label="关闭" :disabled="archiveLoading || busy" @click="cancelArchive">×</button>
        <span class="card-kicker">归档确认</span><h2 id="archive-title">将会话移出最近列表？</h2>
        <p class="archive-title-preview">{{ archiveTarget.title }}</p>
        <p class="muted">归档会保留会话及其消息；不会取消已经提交的业务任务。此操作需要服务端确认。</p>
        <div v-if="archiveLoading" class="archive-preview-state" role="status">正在读取确认预览…</div>
        <p v-if="archiveNotice" class="archive-notice" role="status">{{ archiveNotice }}</p>
        <template v-if="!archiveLoading && confirmationStore.preview">
          <dl v-if="confirmationStore.preview.summary.length" class="archive-summary"><div v-for="(item, index) in confirmationStore.preview.summary" :key="`${item.label}-${index}`"><dt>{{ item.label }}</dt><dd>{{ item.value }}<template v-if="item.unit"> {{ item.unit }}</template></dd></div></dl>
          <p v-for="blocker in confirmationStore.preview.blockers" :key="blocker.code" class="archive-blocker" role="alert">{{ blocker.message }}</p>
          <p v-if="!confirmationStore.preview.can_confirm" class="archive-blocker" role="alert">当前服务端状态不允许确认归档。</p>
        </template>
        <div class="dialog-actions"><button class="button secondary" :disabled="archiveLoading || busy" @click="cancelArchive">取消</button><button class="button primary" :disabled="archiveLoading || busy || !confirmationStore.preview?.can_confirm" @click="confirmArchive">{{ archiveLoading || busy ? '处理中…' : '确认归档' }}</button></div>
      </section>
    </div>
    <div v-if="deleteTarget" class="modal-scrim" @click.self="cancelDelete">
      <section ref="deleteDialog" class="archive-dialog delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title" tabindex="-1" @keydown.esc.stop.prevent="cancelDelete" @keydown="trapDeleteFocus">
        <button class="dialog-close" aria-label="关闭删除确认" :disabled="deleteLoading || busy" @click="cancelDelete">×</button>
        <span class="card-kicker">不可恢复的删除</span><h2 id="delete-title">删除聊天？</h2>
        <p class="archive-title-preview">{{ deleteTarget.title || '未命名会话' }}</p>
        <p class="delete-warning">删除聊天内容，无法从应用恢复；预测记录和业务任务保留。</p>
        <p class="muted">不会自动停止任何运行；若会话仍有未结束的回答、尝试或助手任务，请先停止回答并等待终态。删除后，助手会话资源返回 404。</p>
        <div v-if="deleteLoading" class="archive-preview-state" role="status">{{ deleteAttempt?.idempotencyKey ? '正在重试同一删除请求…' : '正在读取删除确认…' }}</div>
        <p v-if="deleteNotice" class="archive-notice" role="status">{{ deleteNotice }}</p>
        <template v-if="!deleteLoading && confirmationStore.preview?.resource_ref.type === 'conversation' && confirmationStore.preview.resource_ref.id === deleteTarget.id && confirmationStore.preview.action === 'conversation.delete'">
          <dl v-if="confirmationStore.preview.summary.length" class="archive-summary"><div v-for="(item, index) in confirmationStore.preview.summary" :key="`${item.label}-${index}`"><dt>{{ item.label }}</dt><dd>{{ item.value }}<template v-if="item.unit"> {{ item.unit }}</template></dd></div></dl>
          <p v-for="blocker in confirmationStore.preview.blockers" :key="blocker.code" class="archive-blocker" role="alert">{{ blocker.message }}</p>
          <p v-if="!confirmationStore.preview.can_confirm" class="archive-blocker" role="alert">服务端当前不允许删除此聊天。</p>
        </template>
        <div class="dialog-actions">
          <button v-if="!deleteLoading && !confirmationStore.preview" class="button secondary" :disabled="busy" @click="retryDeletePreview">重试读取确认</button>
          <button data-delete-cancel class="button secondary" :disabled="deleteLoading || busy" @click="cancelDelete">取消</button>
          <button class="button danger" :disabled="deleteLoading || busy || !confirmationStore.preview?.can_confirm" @click="confirmDelete">{{ deleteLoading && deleteAttempt?.idempotencyKey ? '正在重试…' : deleteAttempt?.idempotencyKey ? '使用同一确认重试' : '确认删除聊天' }}</button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
:global(*) { box-sizing: border-box; }
.assistant-page { min-height: 100%; padding: 26px; color: var(--text); background: transparent; }
.assistant-header { display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; margin-bottom: 20px; }
.eyebrow { margin: 0 0 7px; color: #1688e8; font-size: 11px; font-weight: 800; letter-spacing: .14em; }
h1 { margin: 0; font-size: 28px; letter-spacing: -.02em; }
.subtitle { margin: 8px 0 0; color: #6c8093; }
.capability-panel { margin: 0 0 18px; padding: 12px 14px; border: 1px solid #dbe7ef; border-radius: 12px; background: rgba(255,255,255,.82); }
.capability-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 6px; color: #3c5b70; font-size: 12px; }
.capability-heading span { color: #8193a1; font-size: 11px; }
.capability-panel .muted, .capability-error { margin: 4px 0 0; font-size: 12px; overflow-wrap: anywhere; }
.capability-error { color: #a1463d; }
.capability-list { display: flex; flex-wrap: wrap; gap: 7px; margin: 8px 0 0; padding: 0; list-style: none; }
.capability-list li { display: inline-flex; align-items: center; gap: 8px; max-width: 100%; padding: 6px 9px; border: 1px solid #dce7ee; border-radius: 999px; background: #f8fbfd; color: #416177; font-size: 11px; }
.capability-list small { color: #8193a1; white-space: nowrap; }
.header-actions { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; justify-content: flex-end; }
.context-chip, .status-pill { padding: 6px 10px; border: 1px solid #b7e2cd; border-radius: 999px; background: #effaf4; color: #18794e; font-size: 12px; font-weight: 700; }
.context-chip.blocked { border-color: #f0c7ba; background: #fff5f1; color: #ac543b; }
.button { min-height: 36px; padding: 0 14px; border-radius: 9px; border: 1px solid transparent; font-weight: 700; transition: .15s ease; }
.button:disabled { cursor: not-allowed; opacity: .5; }
.button.primary { color: #fff; background: var(--primary); box-shadow: 0 5px 14px rgba(24,144,255,.18); }
.button.primary:hover:not(:disabled) { background: var(--primary-hover); }
.button.secondary { color: #486277; border-color: #d1deea; background: #fff; }
.button.small { min-height: 31px; padding: 0 10px; font-size: 12px; }
.error-banner, .blocking-card { padding: 12px 15px; border: 1px solid #efc9bd; border-radius: 11px; background: #fff7f4; color: #a64d3c; }
.blocking-card { margin-bottom: 16px; }
.blocking-card p { margin: 5px 0 0; }
.assistant-shell { display: grid; grid-template-columns: 240px minmax(0, 1fr) 260px; min-height: 680px; overflow: hidden; border: 1px solid #d6e2ec; border-radius: 18px; background: #fff; box-shadow: 0 14px 40px rgba(32,66,98,.08); }
.assistant-shell, .conversation-panel, .chat-panel, .insight-panel, .message-scroll, .message-body, .assistant-bubble { min-width: 0; }
.conversation-panel, .insight-panel { background: #f8fbfd; }
.conversation-panel { border-right: 1px solid #dce7ef; padding: 18px 12px; }
.insight-panel { border-left: 1px solid #dce7ef; padding: 18px 14px; }
.panel-title { display: flex; justify-content: space-between; align-items: center; padding: 0 6px 10px; color: #36536a; font-size: 13px; font-weight: 800; }
.count { color: #8aa0b2; font-weight: 500; }
.conversation-item { display: flex; align-items: center; gap: 9px; width: 100%; margin: 3px 0; padding: 10px 8px; border: 1px solid transparent; border-radius: 11px; background: transparent; color: #355166; text-align: left; }
.conversation-item:hover, .conversation-item.selected { border-color: #c5e0f4; background: #eaf6ff; }
.conversation-dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 50%; background: #77a1bb; }
.conversation-dot.active { background: #22a06b; }.conversation-dot.archived { background: #a8b8c4; }
.conversation-copy { display: grid; min-width: 0; flex: 1; gap: 3px; }.conversation-copy strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }.conversation-copy small { color: #91a1af; font-size: 11px; }
.run-mark { color: #1688e8; font-size: 12px; }
.empty-small { padding: 22px 8px; color: #91a1af; font-size: 12px; }
.chat-panel { display: flex; min-width: 0; flex-direction: column; background: #fff; }
.chat-heading { display: flex; align-items: center; justify-content: space-between; min-height: 65px; padding: 14px 20px; border-bottom: 1px solid #e2eaf0; }.chat-heading div { display: grid; gap: 3px; }.chat-heading span { color: #8b9aa8; font-size: 12px; }
.status-pill { border-color: #cbdff0; background: #f0f7fc; color: #3a6d92; font-size: 11px; }.status-pill.running, .status-pill.queued { border-color: #b7d9f3; background: #edf7ff; color: #1671b8; }.status-pill.waiting_confirmation { border-color: #efd49e; background: #fff8e8; color: #98640d; }
.message-scroll { min-height: 330px; flex: 1; overflow: auto; padding: 22px 22px 14px; }
.welcome-state { display: grid; justify-items: center; max-width: 470px; margin: 80px auto; color: #6d8192; text-align: center; }.welcome-state h2 { margin: 13px 0 4px; color: #27465d; font-size: 19px; }.welcome-state p { margin: 0 0 18px; }.welcome-icon { display: grid; width: 40px; height: 40px; place-items: center; border-radius: 13px; color: #1688e8; background: #e8f5ff; font-size: 20px; }
.message-row { display: flex; gap: 10px; max-width: 750px; margin-bottom: 20px; }.message-row.user { margin-left: auto; flex-direction: row-reverse; }.avatar { display: grid; width: 28px; height: 28px; flex: 0 0 auto; place-items: center; border-radius: 9px; color: #fff; background: #6b91aa; font-size: 12px; font-weight: 800; }.assistant .avatar { background: #1688e8; }
.message-body { min-width: 0; max-width: 100%; }.message-meta { display: flex; align-items: baseline; gap: 8px; margin: 0 3px 5px; font-size: 12px; }.message-meta time { color: #9aabb8; font-size: 11px; }.user .message-meta { justify-content: flex-end; }.message-bubble { max-width: 100%; border-radius: 12px; padding: 11px 13px; overflow-wrap: anywhere; }.user-bubble { color: #21506f; background: #eaf5fc; }.assistant-bubble { border: 1px solid #e2eaf0; background: #fbfdff; overflow-wrap: anywhere; }.message-block + .message-block { margin-top: 10px; }
.result-card { padding: 12px; border: 1px solid #cfe2f2; border-radius: 10px; background: #f7fbff; }.result-card strong { display: block; color: #294c66; }.result-card p { margin: 5px 0 9px; color: #668096; font-size: 12px; }.card-kicker { display: block; margin-bottom: 3px; color: #1688e8; font-size: 10px; font-weight: 800; letter-spacing: .08em; }.text-button { padding: 0; border: 0; color: #1475bd; background: transparent; font-size: 12px; font-weight: 700; }.confirmation-card { border-color: #edd9a8; background: #fffaf0; }.confirmation-card .card-kicker { color: #a16a0c; }.blocked-note { color: #a16a0c; font-size: 12px; }.success-note { color: #18794e; font-size: 12px; font-weight: 700; }.warning-card { padding: 8px 10px; border-radius: 8px; color: #976219; background: #fff8e8; font-size: 12px; }.muted { color: #8b9aa8; font-size: 12px; }
.progress-panel { margin: 0 20px 10px; padding: 11px 13px; border: 1px solid #d5e4ef; border-radius: 11px; background: #f7fbfe; }.progress-heading { display: flex; justify-content: space-between; margin-bottom: 7px; color: #35556c; font-size: 12px; }.progress-heading span { color: #6f8da1; }.tool-list { display: grid; gap: 5px; }.tool-row, .event-row { display: flex; align-items: center; gap: 7px; color: #526f83; font-size: 12px; }.tool-status { margin-left: auto; color: #93a4b0; font-size: 11px; }.tool-state { width: 7px; height: 7px; border-radius: 50%; background: #91a6b5; }.tool-state.running, .tool-state.validating { background: #1688e8; }.tool-state.succeeded { background: #22a06b; }.tool-state.failed, .tool-state.rejected { background: #d35656; }.event-row { margin-top: 5px; color: #8496a4; }.event-row span:last-child { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.recovery-actions { margin-top: 9px; }
.tool-result-card { margin-top: 10px; padding: 10px; border: 1px solid #cfe2f2; border-radius: 9px; background: #fff; }.tool-result-card.data { border-color: #bfe4db; background: #f5fcfa; }.tool-result-card.model { border-color: #d8ccf2; background: #faf8ff; }.tool-result-card strong { display: block; color: #31546b; font-size: 12px; }.tool-result-card dl { display: grid; grid-template-columns: 1fr 1fr; gap: 5px 10px; margin: 8px 0 0; }.tool-result-card dl div { min-width: 0; }.tool-result-card dt { overflow: hidden; color: #8b9ca9; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }.tool-result-card dd { overflow: hidden; margin: 1px 0 0; color: #42657b; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.tool-result-card dt, .tool-result-card dd { min-width: 0; overflow: visible; overflow-wrap: anywhere; text-overflow: clip; white-space: normal; }.tool-result-card .wrap-result-field { min-width: 0; }
.draft-card-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }.draft-card-heading .card-kicker { margin-bottom: 0; }.draft-query-error { margin: 8px 0 0; color: #a1463d; font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.prediction-detail-fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 6px 10px; margin: 8px 0; }.prediction-detail-fields div { min-width: 0; }.prediction-detail-fields dt { color: #8b9ca9; font-size: 10px; }.prediction-detail-fields dd { margin: 2px 0 0; color: #42657b; font-size: 12px; overflow-wrap: anywhere; }.prediction-rf-list,.prediction-warning-list { display: grid; gap: 4px; margin: 8px 0; padding-left: 18px; color: #42657b; font-size: 12px; }.prediction-warning-list { color: #8a641e; }.prediction-detail-error { margin: 8px 0; color: #a1463d; font-size: 12px; overflow-wrap: anywhere; }
.failure-notice { margin-top: 10px; padding: 10px; border: 1px solid #efc9bd; border-radius: 9px; background: #fff7f4; }.failure-notice strong { display: block; color: #a64d3c; font-size: 12px; }.failure-notice p { margin: 4px 0 0; color: #8d5e53; font-size: 12px; }
.assistant-page { display: flex; height: 100%; min-height: 0; flex-direction: column; gap: 10px; padding: 12px 20px; overflow: hidden; }
.assistant-header { min-height: 42px; align-items: center; margin: 0; }
.assistant-brand { display: flex; align-items: center; gap: 10px; color: #294c66; }
.assistant-brand strong { font-size: 15px; }
.brand-mark { display: grid; width: 28px; height: 28px; place-items: center; border-radius: 9px; background: #e8f5ff; color: #1688e8; }
.header-actions { gap: 8px; }
.context-chip { padding: 5px 9px; font-size: 11px; }
.icon-button { display: inline-grid; width: 34px; height: 34px; place-items: center; border: 1px solid #dce7ee; border-radius: 9px; color: #49677d; background: #fff; font-size: 16px; }
.icon-button:disabled { opacity: .55; }
.mobile-menu-button, .chat-sidebar-toggle { display: none; }
.assistant-shell { display: grid; min-height: 0; height: 100%; flex: 1; grid-template-columns: 260px minmax(0, 1fr); grid-template-rows: minmax(0, 1fr) auto; overflow: hidden; border-radius: 14px; }
.sidebar-collapsed .assistant-shell { grid-template-columns: 0 minmax(0, 1fr); }
.conversation-panel[aria-hidden="true"] { display: none; }
.conversation-panel { min-height: 0; padding: 12px 10px; overflow: auto; }
.sidebar-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.sidebar-heading .panel-title { flex: 1; padding: 0 4px 10px; }
.new-conversation-button { margin: 0 0 8px; white-space: nowrap; }
.conversation-search { display: flex; align-items: center; gap: 7px; height: 36px; padding: 0 9px; border: 1px solid #e0e8ee; border-radius: 9px; color: #8b9aa8; background: #fff; }
.conversation-search input { width: 100%; min-width: 0; border: 0; outline: 0; color: #355166; background: transparent; font: inherit; font-size: 12px; }
.conversation-tabs { display: flex; gap: 4px; margin: 9px 0 10px; padding: 3px; border-radius: 9px; background: #edf3f7; }
.conversation-tabs button { flex: 1; padding: 6px; border: 0; border-radius: 7px; color: #718698; background: transparent; font-size: 11px; }
.conversation-tabs button.selected { color: #31546b; background: #fff; box-shadow: 0 1px 4px #294c6617; }
.conversation-group h3 { margin: 14px 7px 5px; color: #91a1af; font-size: 10px; font-weight: 700; }
.conversation-entry { position: relative; display: flex; align-items: center; min-width: 0; margin: 2px 0; border: 1px solid transparent; border-radius: 9px; }
.conversation-entry.selected, .conversation-entry:hover { border-color: #d7e8f4; background: #edf7fd; }
.conversation-item { min-width: 0; margin: 0; padding: 8px 7px; border: 0; flex: 1; }
.conversation-item:hover, .conversation-item.selected { border-color: transparent; background: transparent; }
.conversation-copy strong { font-size: 12px; }
.conversation-menu { position: relative; margin-right: 5px; color: #718698; }
.conversation-menu summary { width: 24px; height: 28px; display: grid; place-items: center; border-radius: 6px; list-style: none; cursor: pointer; }
.conversation-menu summary::-webkit-details-marker { display: none; }
.conversation-menu summary:hover { background: #dfeef7; }
.menu-popover { position: absolute; z-index: 5; top: 28px; right: 0; display: grid; width: 138px; padding: 5px; border: 1px solid #dce7ee; border-radius: 9px; background: #fff; box-shadow: 0 8px 22px #294c661c; }
.menu-popover button { padding: 8px; border: 0; border-radius: 6px; color: #355166; background: transparent; text-align: left; font-size: 11px; }
.menu-popover button:hover:not(:disabled) { background: #f0f6fa; }
.menu-popover button:disabled { color: #a8b5bf; }
.rename-form { display: flex; gap: 4px; width: 100%; padding: 0 5px 6px; }
.rename-form input { min-width: 0; width: 100%; padding: 6px; border: 1px solid #c8dae6; border-radius: 6px; }
.load-more-button { width: 100%; padding: 9px; border: 0; color: #55758a; background: transparent; font-size: 11px; }
.chat-panel { min-height: 0; height: 100%; }
.chat-heading { min-height: 58px; padding: 10px 22px; }
.chat-title { min-width: 0; margin-right: auto; }
.chat-title strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.message-scroll { min-height: 0; padding: 24px max(24px, calc((100% - 850px) / 2)) 16px; scroll-behavior: auto; }
.message-row { width: 100%; max-width: 850px; }
.message-body { width: 100%; }
.user-message-text { white-space: pre-wrap; }
.message-action { margin-left: auto; padding: 2px 5px; border: 0; color: #8b9aa8; background: transparent; font-size: 10px; }
.pending-message { opacity: .8; }
.welcome-state { margin: auto; padding: 30px 0; }
.prompt-examples { display: flex; flex-wrap: wrap; justify-content: center; gap: 7px; margin: 0 0 18px; }
.prompt-examples button { padding: 7px 10px; border: 1px solid #d9e6ee; border-radius: 999px; color: #49677d; background: #fff; font-size: 11px; }
.progress-panel { flex: 0 1 auto; max-height: min(38vh, 300px); overflow: auto; }
.progress-heading { position: sticky; top: -11px; z-index: 1; display: flex; align-items: center; justify-content: space-between; padding: 0 0 6px; background: #f7fbfe; list-style: none; cursor: pointer; }
.progress-heading::-webkit-details-marker { display: none; }
.composer { position: relative; width: min(850px, calc(100% - 32px)); margin: 0 auto 14px; padding: 10px 12px; border: 1px solid #d6e2ec; border-radius: 14px; box-shadow: 0 5px 20px #294c660d; }
.composer textarea { display: block; width: 100%; min-height: 25px; max-height: 160px; padding: 2px 0; overflow: hidden; resize: none; border: 0; outline: 0; color: #294c66; background: transparent; font: inherit; }
.composer-footer { margin-top: 8px; }
.composer-footer > span { max-width: 65%; font-size: 10px; }
.jump-latest { position: absolute; z-index: 2; right: 50%; bottom: calc(100% + 12px); transform: translateX(50%); padding: 7px 11px; border: 1px solid #d6e2ec; border-radius: 999px; color: #31546b; background: white; box-shadow: 0 4px 14px #294c6617; font-size: 11px; }
.insight-details { grid-column: 1 / -1; min-width: 0; border-top: 1px solid #e3ebf1; background: #f8fbfd; }
.insight-details > summary { padding: 10px 15px; color: #55758a; font-size: 11px; cursor: pointer; }
.insight-panel { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 12px; padding: 0 14px 14px; border: 0; }
.capability-panel { margin: 0; }
.sidebar-scrim { display: none; }
.platform-navigation-trigger { margin-right: 2px; }
.modal-scrim { position: fixed; z-index: 40; inset: 0; display: grid; place-items: center; padding: 16px; background: #142c3e72; }
.archive-dialog { position: relative; width: min(480px, 100%); max-height: 85dvh; padding: 22px; overflow: auto; border: 1px solid #dbe7ef; border-radius: 15px; background: #fff; box-shadow: 0 20px 60px #142c3e40; }
.button.danger { color: #fff; border-color: #b93d36; background: #c5443c; box-shadow: 0 5px 14px #b73c3426; }
.button.danger:hover:not(:disabled) { background: #a9322c; }
.delete-warning { color: #9c302b; font-weight: 800; }
.archive-dialog h2 { margin: 4px 28px 7px 0; color: #294c66; font-size: 18px; }
.archive-dialog p { line-height: 1.5; }
.archive-title-preview { margin: 0; color: #355166; font-weight: 700; }
.dialog-close { position: absolute; top: 11px; right: 12px; width: 30px; height: 30px; border: 0; border-radius: 8px; color: #6c8093; background: #f3f7fa; font-size: 20px; }
.archive-preview-state { padding: 12px 0; color: #668096; font-size: 12px; }
.archive-summary { display: grid; gap: 8px; margin: 12px 0; }
.archive-summary div { display: flex; justify-content: space-between; gap: 12px; padding-bottom: 7px; border-bottom: 1px solid #edf1f4; }
.archive-summary dt { color: #7c909f; font-size: 12px; }
.archive-summary dd { margin: 0; color: #355166; font-size: 12px; text-align: right; }
.archive-blocker { margin: 7px 0; color: #a1463d; font-size: 12px; }
.archive-notice { padding: 8px; border-radius: 8px; color: #855d16; background: #fff8e8; font-size: 12px; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 18px; }
@media (max-width: 700px) {
  .assistant-page { height: 100%; min-height: 0; padding: 8px; gap: 7px; overflow: hidden; }
  .assistant-header { min-height: 40px; }
  .assistant-brand { gap: 6px; }
  .assistant-brand strong { font-size: 13px; }
  .brand-mark { width: 25px; height: 25px; }
  .platform-navigation-trigger { width: 30px; height: 30px; }
  .mobile-menu-button, .chat-sidebar-toggle { display: inline-grid; }
  .desktop-menu-button, .context-chip { display: none; }
  .assistant-shell, .sidebar-collapsed .assistant-shell { position: relative; grid-template-columns: minmax(0, 1fr); grid-template-rows: minmax(0, 1fr) auto; min-height: 0; height: 100%; }
  .conversation-panel, .conversation-panel[aria-hidden="true"] { position: fixed; z-index: 20; inset: 0 auto 0 0; display: block; width: min(300px, 84vw); height: 100dvh; transform: translateX(-105%); transition: transform .18s ease; box-shadow: 10px 0 30px #142c3e26; }
  .mobile-sidebar-open .conversation-panel { transform: translateX(0); }
  .sidebar-scrim { position: fixed; z-index: 19; inset: 0; display: block; border: 0; background: #142c3e66; }
  .chat-panel { grid-column: 1; }
  .chat-heading { min-height: 52px; padding: 8px 10px; gap: 9px; }
  .chat-heading .status-pill { font-size: 10px; }
  .message-scroll { padding: 16px 10px 12px; }
  .message-row { gap: 7px; margin-bottom: 16px; }
  .avatar { width: 25px; height: 25px; }
  .message-bubble { padding: 9px 10px; }
  .composer { width: calc(100% - 16px); margin: 0 auto 8px; padding: 9px 10px; }
  .composer-footer { align-items: center; }
  .composer-footer > span { max-width: 50%; }
  .composer-footer .button { min-height: 33px; padding: 0 10px; }
  .progress-panel { margin: 0 9px 8px; max-height: 30vh; }
  .insight-details { grid-column: 1; }
  .insight-panel { grid-template-columns: 1fr; }
}
.composer { padding: 12px 20px 17px; border-top: 1px solid #e2eaf0; }.composer textarea { display: block; width: 100%; resize: vertical; border: 1px solid #cbdbe7; border-radius: 10px; outline: 0; padding: 10px 12px; color: #27465d; background: #fff; }.composer textarea:focus { border-color: #72b5e5; box-shadow: 0 0 0 3px #e5f4ff; }.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 8px; color: #93a2af; font-size: 11px; }.composer-footer div { display: flex; gap: 7px; }
.side-section + .side-section { margin-top: 22px; }.side-card { margin: 8px 0; padding: 10px; border: 1px solid #dbe7ef; border-radius: 10px; background: #fff; }.side-card strong { color: #3c5b70; font-size: 12px; }.side-card p { margin: 4px 0 0; color: #8294a2; font-size: 11px; line-height: 1.5; }.muted-card { background: #fbfdfe; }.blocked-card { border-color: #ecd7ca; background: #fffaf7; }.blocked-card strong { color: #98624f; }.scope-section ul { margin: 0; padding-left: 20px; color: #5c7487; font-size: 12px; line-height: 1.9; }.scope-section .muted { display: block; margin: 10px 6px; line-height: 1.6; }
.summary-query-section { min-width: 0; }.summary-query-card { min-width: 0; margin-top: 10px; padding: 10px; border: 1px solid #dbe7ef; border-radius: 10px; background: #fff; }.summary-query-card > strong { color: #3c5b70; font-size: 12px; }.summary-query-card > .button { margin-top: 7px; }.summary-filter { display: grid; gap: 5px; margin: 9px 0 7px; color: #5d7385; font-size: 11px; }.summary-filter select { width: 100%; min-width: 0; min-height: 34px; padding: 5px 8px; border: 1px solid #d3e0e9; border-radius: 7px; background: #fff; color: #344f63; }.summary-result { margin-top: 9px; padding-top: 8px; border-top: 1px solid #e5edf2; color: #5f7587; font-size: 11px; line-height: 1.55; overflow-wrap: anywhere; }.summary-result p { margin: 5px 0; }.summary-result p strong { color: #314f64; }.summary-result p span { margin-left: 4px; color: #8294a2; }.summary-stat-list { margin: 6px 0; }.summary-stat-list > div { display: grid; grid-template-columns: minmax(65px, .45fr) minmax(0, 1fr); gap: 6px; padding: 4px 0; border-top: 1px solid #eef2f5; }.summary-stat-list dt { color: #5c7487; overflow-wrap: anywhere; }.summary-stat-list dd { margin: 0; color: #334f63; overflow-wrap: anywhere; }.summary-source-ids { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin-top: 7px; }.summary-source-ids code { max-width: 100%; overflow-wrap: anywhere; padding: 2px 4px; border-radius: 4px; background: #f1f6f9; color: #4e687a; font-size: 10px; }.summary-source-ids small { width: 100%; color: #8294a2; }.summary-query-error { color: #a1463d; font-size: 11px; overflow-wrap: anywhere; }
@media (max-width: 1050px) { .assistant-page { padding: 18px; }.assistant-shell { grid-template-columns: minmax(170px, 200px) minmax(0, 1fr); }.insight-panel { display: block; grid-column: 1 / -1; padding: 14px; border-top: 1px solid #dce7ef; border-left: 0; }.insight-panel > .side-section:first-child { display: none; }.summary-query-section { margin-top: 0; } }
@media (max-width: 700px) { .assistant-page { padding: 10px; }.assistant-header { display: block; }.header-actions { justify-content: flex-start; margin-top: 15px; }.assistant-shell { grid-template-columns: minmax(0, 1fr); min-height: 720px; border-radius: 12px; }.chat-panel { min-width: 0; }.message-scroll { padding: 14px 10px 10px; }.message-row { min-width: 0; }.message-bubble { padding: 9px 10px; }.composer { padding: 12px 10px; }.composer-footer { align-items: flex-end; }.composer-footer > span { max-width: 42%; } }
</style>
