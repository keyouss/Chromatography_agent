import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, ApiClientError, createIdempotencyKey, type ApiResult } from '@/core/api/client'
import { createEventStream, EventStreamError } from '@/core/api/sse'
import { useConfirmationStore } from '@/stores/confirmations'
import { requestAccessContext, resolveProjectId } from '@/core/project-context'
import type { StreamEvent } from '@/core/types'
import { applyAssistantTextDelta, assistantTextProjectionFromSnapshot, type AssistantTextProjection } from './stream-projection'
import type {
  ContractAccessContext,
  ContractAgentRun,
  ContractAssistantEvent,
  ContractAssistantEventPage,
  ContractCancelAgentRunBody,
  ContractConfirmation,
  ContractConversation,
  ContractConversationCreateBody,
  ContractConversationDelete,
  ContractConversationDeleted,
  ContractMessage,
  ContractMessageAccepted,
  ContractPrediction,
  ContractResumeBody,
  ContractToolCall,
  ContractUserMessageCreateBody,
  ContractAssistantModelRegistrationDraft,
  ContractDataDomain,
  ContractDataVersionSummary,
  ContractPredictionRecordSummary,
  ContractPriorityRegistrationDraftRequest,
  ContractPriorityTool,
  ContractPriorityTools,
  ContractTypeSummary,
} from '@/core/contract-types'

const PERSIST_KEY = 'chrom-platform.assistant'
const TERMINAL_RUNS = new Set(['SUCCEEDED', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'])
const FAILED_RUNS = new Set(['FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'])
const TERMINAL_RUN_EVENTS = new Set(['run.completed', 'run.failed', 'run.canceled', 'run.interrupted'])

export const ASSISTANT_TOOL_GROUPS = {
  prediction: new Set(['get_prediction_capabilities', 'create_prediction', 'get_prediction']),
  data: new Set(['get_data_version_summary', 'get_prediction_record_summary']),
  model: new Set(['list_models', 'get_model_card', 'create_registration_draft', 'get_registration_draft']),
  structure: new Set(['normalize_structure']),
} as const

export function assistantToolGroup(toolName: string): 'prediction' | 'data' | 'model' | 'structure' | null {
  const name = toolName.toLowerCase()
  for (const [group, names] of Object.entries(ASSISTANT_TOOL_GROUPS)) if (names.has(name as never)) return group as 'prediction' | 'data' | 'model' | 'structure'
  return null
}

export interface StructureToolSummary {
  structureRef: string | null
  canonicalSmiles: string | null
  stereochemistryStatus: string | null
  hasStereochemistry: boolean | null
  warnings: string[]
}

function publicStructureWarning(value: unknown): string | null {
  const text = typeof value === 'string' ? value : value && typeof value === 'object' && !Array.isArray(value) && typeof (value as { message?: unknown }).message === 'string' ? (value as { message: string }).message : null
  if (!text || text.length > 500 || /(traceback|stack trace|internal exception|valueerror|typeerror|keyerror|runtimeerror|api[_ -]?key|access[_ -]?token|secret|password|[A-Za-z]:\\|\/(?:srv|app|home|tmp|var|etc)\b)/i.test(text)) return null
  return text
}

function publicStructureRef(value: unknown): string | null {
  return typeof value === 'string' && /^[A-Za-z][A-Za-z0-9_.:-]{0,95}$/.test(value) ? value : null
}

export function structureToolSummary(result: unknown): StructureToolSummary | null {
  if (!result || typeof result !== 'object' || Array.isArray(result)) return null
  const record = result as Record<string, unknown>
  const rawRef = record.structure_ref
  const structureRef = typeof rawRef === 'string'
    ? publicStructureRef(rawRef)
    : rawRef && typeof rawRef === 'object' && !Array.isArray(rawRef) && typeof (rawRef as { id?: unknown }).id === 'string'
      ? publicStructureRef((rawRef as { id: string }).id)
      : null
  const warnings = Array.isArray(record.warnings)
    ? record.warnings.flatMap((warning) => { const safe = publicStructureWarning(warning); return safe ? [safe] : [] }).slice(0, 8)
    : []
  return {
    structureRef,
    canonicalSmiles: typeof record.canonical_smiles === 'string' ? record.canonical_smiles : null,
    stereochemistryStatus: typeof record.stereochemistry_status === 'string' ? record.stereochemistry_status : null,
    hasStereochemistry: typeof record.has_stereochemistry === 'boolean' ? record.has_stereochemistry : null,
    warnings,
  }
}

export function assistantRegistrationDraftPath(resourceId: string, projectId: string): string {
  return `/assistant/priority/model-registration-drafts/${encodeURIComponent(resourceId)}?project_id=${encodeURIComponent(projectId)}`
}

const REGISTRATION_DRAFT_TOOLS = new Set(['create_registration_draft', 'get_registration_draft'])
const SAFE_REGISTRATION_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/
const SAFE_PREDICTION_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$/

export function assistantPredictionResourceId(call: Pick<ContractToolCall, 'tool_name' | 'status' | 'resource_ref'>): string | null {
  if (call.status !== 'SUCCEEDED' || call.tool_name !== 'create_prediction') return null
  const resource = call.resource_ref
  if (!resource || resource.type !== 'prediction' || !SAFE_PREDICTION_ID.test(resource.id)) return null
  return resource.id
}

export function assistantRegistrationDraftResourceId(call: Pick<ContractToolCall, 'tool_name' | 'status' | 'resource_ref'>): string | null {
  if (call.status !== 'SUCCEEDED' || !REGISTRATION_DRAFT_TOOLS.has(call.tool_name)) return null
  const resource = call.resource_ref
  if (!resource || resource.type !== 'model_registration' || !SAFE_REGISTRATION_ID.test(resource.id)) return null
  return resource.id
}

export function makeResumeBody(reason: 'recovery' | 'confirmation', confirmationId?: string): ContractResumeBody {
  if (reason === 'confirmation') { if (!confirmationId) throw new Error('confirmation resume requires confirmation_id'); return { reason, confirmation_id: confirmationId } }
  return { reason }
}

export interface AssistantFailureNotice {
  code: string
  summary: string
}

export interface ScopedAssistantTextProjection extends AssistantTextProjection {
  conversation_id: string
  run_id: string
  incomplete: boolean
}

const MESSAGE_SNAPSHOT_REPLACED = 'MESSAGE_SNAPSHOT_REPLACED'

const PUBLIC_FAILURE_SUMMARIES: Record<string, string> = {
  PROVIDER_REQUEST_REJECTED: '模型服务拒绝了请求',
  PROVIDER_RATE_LIMITED: '模型服务请求受限，请稍后重试',
  PROVIDER_UNAVAILABLE: '模型服务暂不可用',
  PROVIDER_TRANSPORT_REJECTED: '模型服务连接被拒绝',
}

function publicFailureSummary(code: string, message?: string | null): string {
  const normalized = message?.trim()
  return normalized || PUBLIC_FAILURE_SUMMARIES[code] || '服务端未能完成助手请求'
}

function noticeFromError(error: ContractAgentRun['error']): AssistantFailureNotice | null {
  if (!error?.code) return null
  return { code: error.code, summary: publicFailureSummary(error.code, error.message) }
}

export function assistantFailureNotice(
  run: (Pick<ContractAgentRun, 'error'> & Partial<Pick<ContractAgentRun, 'status'>>) | null | undefined,
  historicalEvents: readonly ContractAssistantEvent[] = [],
  liveEvents: readonly ContractAssistantEvent[] = [],
): AssistantFailureNotice | null {
  const fromRun = (!run?.status || FAILED_RUNS.has(run.status)) ? noticeFromError(run?.error) : null
  if (fromRun) return fromRun

  const events = [...historicalEvents, ...liveEvents]
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (/\.(failed|error)$/.test(event.type)) {
      const fromEvent = noticeFromError(event.data.error)
      if (fromEvent) return fromEvent
    }
    if (/\.(failed|error)$/.test(event.type) && event.data.code) {
      return { code: event.data.code, summary: publicFailureSummary(event.data.code, event.data.message) }
    }
  }
  return null
}

export function toolResultSummaryEntries(call: Pick<ContractToolCall, 'status' | 'result_summary'>): [string, string | number | boolean][] {
  const summary = call.result_summary
  if (!summary || typeof summary !== 'object') return []
  const succeeded = call.status.toUpperCase() === 'SUCCEEDED'
  return Object.entries(summary).filter(([key, value]) => {
    if (/(^|_)(id|hash|etag|token|secret|path|stack|trace)(_|$)/i.test(key)) return false
    if (succeeded && /^(error|error_code|error_message|exception)$/i.test(key)) return false
    return ['string', 'number', 'boolean'].includes(typeof value)
  }).slice(0, 8) as [string, string | number | boolean][]
}

export function canSendAssistantMessage(input: { projectId: string | null; projectSelection?: string; conversationId: string | null; draft: string; runStatus?: string | null }): boolean {
  return Boolean(input.projectId && input.projectSelection === 'resolved' && input.conversationId && input.draft.trim() && (!input.runStatus || TERMINAL_RUNS.has(input.runStatus)))
}

function loadPersisted(): { projectId?: string; conversationId?: string } {
  try { return JSON.parse(localStorage.getItem(PERSIST_KEY) ?? '{}') as { projectId?: string; conversationId?: string } } catch { return {} }
}

export const useAssistantStore = defineStore('assistant', () => {
  const accessContext = ref<ContractAccessContext | null>(null)
  const projectId = ref<string | null>(null)
  const selectedConversationId = ref<string | null>(null)
  const run = ref<ContractAgentRun | null>(null)
  const conversations = ref<ContractConversation[]>([])
  const searchResults = ref<ContractConversation[]>([])
  const archivedConversations = ref<ContractConversation[]>([])
  const messages = ref<ContractMessage[]>([])
  const streamTextProjections = ref<ScopedAssistantTextProjection[]>([])
  const events = ref<ContractAssistantEventPage | null>(null)
  const liveEvents = ref<ContractAssistantEvent[]>([])
  const lastEventSeq = ref('')
  const toolCalls = ref<ContractToolCall[]>([])
  const predictionResults = ref<Record<string, ContractPrediction>>({})
  const registrationDrafts = ref<Record<string, ContractAssistantModelRegistrationDraft>>({})
  const registrationDraftStatus = ref<Record<string, 'loading' | 'ready' | 'error'>>({})
  const registrationDraftErrors = ref<Record<string, string>>({})
  const priorityTools = ref<ContractPriorityTool[]>([])
  const priorityToolsState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const priorityToolsError = ref<string | null>(null)
  const confirmationNotice = ref<string | null>(null)
  const confirmationNoticeRunId = ref<string | null>(null)
  const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const error = ref<string | null>(null)
  const location = ref<string | null>(null)
  let stream: { close: () => void } | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let restoreTimer: ReturnType<typeof setTimeout> | null = null
  let restoreController: AbortController | null = null
  let refreshTimer: ReturnType<typeof setTimeout> | null = null
  let refreshFlight: Promise<void> | null = null
  let refreshFlightScope: string | null = null
  let refreshQueued = false
  let refreshQueuedImmediate = false
  let refreshNextRequest: { id: string; runAtStart: number; conversationAtStart: number; immediate: boolean } | null = null
  let messagesRequestEpoch = 0
  let snapshotRecoveryEpoch = 0
  const snapshotRecovery = new Map<string, { conversation_id: string; run_id: string; message_id: string; block_index: number; token: number }>()
  const seenSeq = new Set<string>()
  let activeRunId: string | null = null
  let eventTrackingRunId: string | null = null
  let conversationGeneration = 0
  const conversationListGeneration = { active: 0, search: 0, archived: 0 }
  let runGeneration = 0
  let streamGeneration = 0
  let sendingMessage = false

  function isCurrentConversation(id: string, generation: number, project = projectId.value): boolean {
    return generation === conversationGeneration && selectedConversationId.value === id && projectId.value === project
  }

  function isCurrentRun(id: string, runAtStart: number, conversationAtStart: number): boolean {
    return runAtStart === runGeneration && conversationAtStart === conversationGeneration && activeRunId === id
  }

  function resetEventTrackingForRun(id: string) {
    if (eventTrackingRunId === id) return
    seenSeq.clear()
    liveEvents.value = []
    lastEventSeq.value = ''
    eventTrackingRunId = id
  }

  function prepareRunAction(id: string): boolean {
    if (activeRunId === id) return true
    if (activeRunId || (run.value && run.value.id !== id)) return false
    activeRunId = id
    return true
  }

  function persist(conversationId = selectedConversationId.value) {
    try { localStorage.setItem(PERSIST_KEY, JSON.stringify({ projectId: projectId.value, conversationId })) } catch { /* private browsing can reject storage */ }
  }

  function clearConversationState() {
    conversationGeneration += 1
    stopEventStream()
    activeRunId = null
    seenSeq.clear()
    eventTrackingRunId = null
    selectedConversationId.value = null
    run.value = null
    messages.value = []
    streamTextProjections.value = []
    snapshotRecovery.clear()
    events.value = null
    liveEvents.value = []
    lastEventSeq.value = ''
    toolCalls.value = []
    predictionResults.value = {}
    registrationDrafts.value = {}
    registrationDraftStatus.value = {}
    registrationDraftErrors.value = {}
    confirmationNotice.value = null
    confirmationNoticeRunId.value = null
    location.value = null
    error.value = null
  }

  async function getAccessContext() {
    const context = await requestAccessContext<ContractAccessContext>()
    accessContext.value = context
    const persisted = loadPersisted()
    const nextProjectId = resolveProjectId(context, persisted.projectId)
    if (projectId.value !== nextProjectId) {
      conversationListGeneration.active += 1
      conversationListGeneration.search += 1
      conversationListGeneration.archived += 1
      conversations.value = []
      searchResults.value = []
      archivedConversations.value = []
      clearConversationState()
    }
    projectId.value = nextProjectId
    persist(persisted.projectId === nextProjectId ? persisted.conversationId ?? null : null)
    return context
  }

  async function listConversations(options: { q?: string; archived?: boolean; cursor?: string; append?: boolean } = {}) {
    if (!projectId.value) {
      if (options.archived) archivedConversations.value = []
      else if (options.q?.trim()) searchResults.value = []
      else conversations.value = []
      clearConversationState()
      persist()
      return null
    }
    const archived = options.archived ?? false
    const requestProjectId = projectId.value
    const searchText = options.q?.trim() ?? ''
    const targetKey: keyof typeof conversationListGeneration = archived ? 'archived' : searchText ? 'search' : 'active'
    const requestGeneration = ++conversationListGeneration[targetKey]
    const query = new URLSearchParams({ project_id: requestProjectId, archived: String(archived) })
    if (searchText) query.set('q', searchText)
    if (options.cursor) query.set('cursor', options.cursor)
    const result = await apiClient.requestWithMeta<ContractConversation[]>(`/conversations?${query.toString()}`)
    if (projectId.value !== requestProjectId || requestGeneration !== conversationListGeneration[targetKey]) return null
    const projectConversations = result.data.filter((item) => item.project_id === requestProjectId)
    const target = archived ? archivedConversations : searchText ? searchResults : conversations
    if (options.append) {
      const known = new Set(target.value.map((item) => item.id))
      target.value = [...target.value, ...projectConversations.filter((item) => !known.has(item.id))]
    } else target.value = projectConversations
    if (archived || searchText || options.append) return result
    const persisted = loadPersisted()
    const selected = projectConversations.find((item) => item.id === persisted.conversationId) ?? null
    if (!selected) {
      clearConversationState()
    } else {
      const selectionChanged = selectedConversationId.value !== selected.id
      if (selectionChanged) clearConversationState()
      selectedConversationId.value = selected.id
    }
    persist()
    return result
  }

  async function renameConversation(id: string, title: string) {
    const requestProjectId = projectId.value
    const conversation = searchResults.value.find((item) => item.id === id && item.project_id === requestProjectId && item.status === 'ACTIVE')
      ?? conversations.value.find((item) => item.id === id && item.project_id === requestProjectId && item.status === 'ACTIVE')
    if (!conversation || conversation.status !== 'ACTIVE') throw new Error('当前会话不可重命名')
    const result = await apiClient.requestWithMeta<ContractConversation>(`/conversations/${encodeURIComponent(id)}`, {
      method: 'PATCH', body: { title }, ifMatch: conversation.etag, idempotencyKey: createIdempotencyKey(),
    })
    if (projectId.value !== requestProjectId || result.data.id !== id || result.data.project_id !== requestProjectId || result.data.status !== 'ACTIVE') throw new Error('服务端返回的会话与当前项目引用不匹配')
    conversations.value = conversations.value.map((item) => item.id === id ? result.data : item)
    searchResults.value = searchResults.value.map((item) => item.id === id ? result.data : item)
    return result.data
  }

  async function archiveConversation(id: string, confirmationId: string) {
    const requestProjectId = projectId.value
    const conversation = searchResults.value.find((item) => item.id === id && item.project_id === requestProjectId && item.status === 'ACTIVE')
      ?? conversations.value.find((item) => item.id === id && item.project_id === requestProjectId && item.status === 'ACTIVE')
    if (!conversation || conversation.status !== 'ACTIVE') throw new Error('当前会话不可归档')
    const result = await apiClient.requestWithMeta<ContractConversation>(`/conversations/${encodeURIComponent(id)}/archive`, {
      method: 'POST', body: { confirmation_id: confirmationId }, idempotencyKey: createIdempotencyKey(),
    })
    if (projectId.value !== requestProjectId || result.data.id !== id || result.data.project_id !== requestProjectId || result.data.status !== 'ARCHIVED') throw new Error('服务端返回的会话与当前项目引用不匹配')
    conversations.value = conversations.value.filter((item) => item.id !== id)
    searchResults.value = searchResults.value.filter((item) => item.id !== id)
    archivedConversations.value = [result.data, ...archivedConversations.value.filter((item) => item.id !== id)]
    if (selectedConversationId.value === id) {
      clearConversationState()
      persist(null)
    }
    return result.data
  }

  async function deleteConversation(id: string, body: ContractConversationDelete, idempotencyKey: string, expectedEtag: string, expectedProjectId: string) {
    const requestProjectId = projectId.value
    const candidates = [...searchResults.value, ...conversations.value, ...archivedConversations.value].filter((item) => item.id === id)
    const conversation = candidates.find((item) => item.project_id === requestProjectId)
    if (!requestProjectId || requestProjectId !== expectedProjectId || !expectedEtag || candidates.some((item) => item.project_id !== requestProjectId) || (conversation && !['ACTIVE', 'ARCHIVED'].includes(conversation.status))) {
      throw new Error('当前项目中找不到可删除的会话')
    }
    const currentRun = run.value
    if (selectedConversationId.value === id && currentRun?.conversation_id === id && !TERMINAL_RUNS.has(currentRun.status)) {
      throw new ApiClientError(409, 'CONVERSATION_BUSY', '请先停止回答并等待运行结束后再删除会话。')
    }
    const result = await apiClient.requestWithMeta<ContractConversationDeleted>(`/conversations/${encodeURIComponent(id)}`, {
      method: 'DELETE', body, ifMatch: expectedEtag, idempotencyKey,
    })
    if (projectId.value !== requestProjectId) throw new Error('项目已切换；未更新当前会话列表。')
    if (result.data.id !== id || result.data.deleted !== true) throw new Error('服务端删除结果与当前会话不匹配')

    conversationListGeneration.active += 1
    conversationListGeneration.search += 1
    conversationListGeneration.archived += 1
    conversations.value = conversations.value.filter((item) => item.id !== id)
    searchResults.value = searchResults.value.filter((item) => item.id !== id)
    archivedConversations.value = archivedConversations.value.filter((item) => item.id !== id)
    if (selectedConversationId.value === id) {
      clearConversationState()
      persist(null)
    }
    return result.data
  }

  async function bootstrap() {
    state.value = 'loading'; error.value = null
    try {
      await getAccessContext()
      await listPriorityTools()
      if (!projectId.value) { state.value = 'ready'; return }
      await listConversations()
      if (selectedConversationId.value) await loadConversation(selectedConversationId.value)
      state.value = 'ready'
    } catch (reason) { state.value = 'error'; error.value = reason instanceof Error ? reason.message : '助手工作区暂不可用'; throw reason }
  }

  async function createConversation(body?: Partial<ContractConversationCreateBody>) {
    if (!projectId.value) throw new Error('服务端未提供可用项目，无法创建会话')
    const currentProjectId = projectId.value
    clearConversationState()
    projectId.value = currentProjectId
    confirmationNotice.value = null; confirmationNoticeRunId.value = null
    const payload: ContractConversationCreateBody = { project_id: projectId.value, mode: 'business', title: body?.title ?? null }
    const result = await apiClient.requestWithMeta<ContractConversation>('/conversations', { method: 'POST', body: payload, idempotencyKey: createIdempotencyKey() })
    if (result.data.project_id !== currentProjectId) throw new Error('服务端返回的会话不属于当前项目')
    conversations.value = [result.data, ...conversations.value]
    selectedConversationId.value = result.data.id
    location.value = result.location ?? null
    persist()
    return result
  }

  async function listMessages(id: string, cursor?: string, signal?: AbortSignal, expectedRun?: { id: string; generation: number; conversationGeneration: number }) {
    const generation = conversationGeneration
    const project = projectId.value
    const requestEpoch = ++messagesRequestEpoch
    const projectionRevisions = new Map(streamTextProjections.value.map((item) => [projectionKey(item.conversation_id, item.run_id, item.message_id, item.block_index), item.revision]))
    const recoveryAtStart = new Map(snapshotRecovery)
    const result = await apiClient.requestWithMeta<ContractMessage[]>(`/conversations/${encodeURIComponent(id)}/messages${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`, { signal })
    const runStillCurrent = !expectedRun || isCurrentRun(expectedRun.id, expectedRun.generation, expectedRun.conversationGeneration)
    if (runStillCurrent && isCurrentConversation(id, generation, project) && requestEpoch === messagesRequestEpoch) {
      messages.value = result.data
      const byMessage = new Map(result.data.map((message) => [message.id, message]))
      const currentProjections = streamTextProjections.value
      const handledKeys = new Set<string>()
      let needsFollowup = false
      for (const [key, recovery] of recoveryAtStart) {
        if (recovery.conversation_id !== id || (expectedRun && recovery.run_id !== expectedRun.id)) continue
        const currentRecovery = snapshotRecovery.get(key)
        if (currentRecovery?.token !== recovery.token) { if (currentRecovery) needsFollowup = true; continue }
        const current = currentProjections.find((item) => projectionKey(item.conversation_id, item.run_id, item.message_id, item.block_index) === key)
        const startedAtRevision = projectionRevisions.get(key)
        if (current && startedAtRevision !== current.revision) { needsFollowup = true; continue }
        const message = byMessage.get(recovery.message_id)
        const block = message?.blocks?.[recovery.block_index]
        if (block?.type !== 'text' || !message || message.role !== 'assistant') continue
        if (message.status === 'FINAL' || message.status === 'FAILED') {
          handledKeys.add(key)
          snapshotRecovery.delete(key)
        } else if (message.status === 'PARTIAL') {
          const replacement: ScopedAssistantTextProjection = {
            ...assistantTextProjectionFromSnapshot(recovery.message_id, recovery.block_index, block.text, (current?.revision ?? 0) + 1),
            conversation_id: recovery.conversation_id,
            run_id: recovery.run_id,
            incomplete: true,
          }
          const index = currentProjections.findIndex((item) => projectionKey(item.conversation_id, item.run_id, item.message_id, item.block_index) === key)
          if (index < 0) currentProjections.push(replacement)
          else currentProjections[index] = replacement
          handledKeys.add(key)
          snapshotRecovery.delete(key)
        }
      }
      streamTextProjections.value = currentProjections.flatMap((item) => {
        const key = projectionKey(item.conversation_id, item.run_id, item.message_id, item.block_index)
        if (handledKeys.has(key)) {
          const recovery = recoveryAtStart.get(key)
          const message = recovery ? byMessage.get(recovery.message_id) : null
          return message?.status === 'FINAL' || message?.status === 'FAILED' ? [] : [item]
        }
        if (item.conversation_id !== id) return [item]
        const message = byMessage.get(item.message_id)
        const block = message?.blocks?.[item.block_index]
        const canonicalText = block?.type === 'text' ? block.text : null
        const startedAtRevision = projectionRevisions.get(key)
        const currentRecovery = snapshotRecovery.get(key)
        const startedRecovery = recoveryAtStart.get(key)
        if (currentRecovery?.token !== startedRecovery?.token) { if (currentRecovery) needsFollowup = true; return [item] }
        if (startedAtRevision !== item.revision) { if (currentRecovery) needsFollowup = true; return [item] }
        if (canonicalText !== null && startedAtRevision === item.revision && (message?.status === 'FINAL' || message?.status === 'FAILED') && canonicalText === item.text) return []
        if (canonicalText !== null && startedAtRevision === item.revision && message?.status === 'PARTIAL' && canonicalText.startsWith(item.text) && Array.from(canonicalText).length > item.codePointLength) {
          return [{ ...item, text: canonicalText, codePointLength: Array.from(canonicalText).length, revision: item.revision + 1 }]
        }
        return [item]
      })
      if (needsFollowup && expectedRun) void requestRunDetails(expectedRun.id, expectedRun.generation, expectedRun.conversationGeneration, true).catch(() => undefined)
    }
    return result
  }

  async function listConversationRuns(id: string) {
    const generation = conversationGeneration
    const project = projectId.value
    const result = await apiClient.requestWithMeta<ContractAgentRun[]>(`/conversations/${encodeURIComponent(id)}/runs`)
    if (!isCurrentConversation(id, generation, project)) return result
    const latest = result.data[0]
    if (latest) await loadRun(latest.id)
    return result
  }

  async function loadConversation(id: string) {
    const conversation = [...searchResults.value, ...conversations.value, ...archivedConversations.value].find((item) => item.id === id)
    if (!conversation || !projectId.value || conversation.project_id !== projectId.value) throw new Error('会话不属于当前项目')
    if (selectedConversationId.value !== id) clearConversationState()
    else stopEventStream()
    const generation = ++conversationGeneration
    const project = projectId.value
    selectedConversationId.value = id; confirmationNotice.value = null; confirmationNoticeRunId.value = null
    persist()
    try { await listMessages(id) } catch (reason) { if (isCurrentConversation(id, generation, project)) throw reason; return }
    if (!isCurrentConversation(id, generation, project)) return
    run.value = null; toolCalls.value = []; stopEventStream()
    if (conversation.current_run_id) await loadRun(conversation.current_run_id)
    else await listConversationRuns(id)
  }

  async function getRun(id: string, signal?: AbortSignal) {
    const generation = runGeneration
    const conversation = conversationGeneration
    const result = await apiClient.requestWithMeta<ContractAgentRun>(`/agent-runs/${encodeURIComponent(id)}`, { signal })
    if (isCurrentRun(id, generation, conversation) && result.data.id === id) run.value = result.data
    return result
  }

  async function loadRun(id: string) {
    if (run.value?.id !== id) {
      streamTextProjections.value = streamTextProjections.value.filter((item) => item.run_id === id)
      for (const [key, recovery] of snapshotRecovery) if (recovery.run_id !== id) snapshotRecovery.delete(key)
    }
    resetEventTrackingForRun(id)
    stopEventStream()
    activeRunId = id
    const generation = runGeneration
    const conversation = conversationGeneration
    if (confirmationNoticeRunId.value && confirmationNoticeRunId.value !== id) { confirmationNotice.value = null; confirmationNoticeRunId.value = null }
    try { await Promise.all([getRun(id), listEvents(id), listToolCalls(id)]) } catch (reason) { if (isCurrentRun(id, generation, conversation)) throw reason; return }
    if (isCurrentRun(id, generation, conversation) && run.value?.id === id && !TERMINAL_RUNS.has(run.value.status)) startEventStream(id, generation, conversation)
  }

  async function sendMessage(id: string, body: ContractUserMessageCreateBody): Promise<ApiResult<ContractMessageAccepted>> {
    if (sendingMessage) throw new Error('消息正在提交，请稍候')
    const conversation = conversations.value.find((item) => item.id === id)
    if (!projectId.value || selectedConversationId.value !== id || !conversation || conversation.project_id !== projectId.value || !['knowledge', 'business'].includes(conversation.mode)) throw new Error('当前会话不属于可用项目')
    if (run.value && !TERMINAL_RUNS.has(run.value.status)) throw new Error('当前会话仍在运行，请等待本轮回答完成')
    const generation = conversationGeneration
    const project = projectId.value
    const { mode: _callerMode, ...messageBody } = body
    sendingMessage = true
    try {
      confirmationNotice.value = null; confirmationNoticeRunId.value = null
      const result = await apiClient.requestWithMeta<ContractMessageAccepted>(`/conversations/${encodeURIComponent(id)}/messages`, { method: 'POST', body: messageBody, idempotencyKey: createIdempotencyKey() })
      if (!isCurrentConversation(id, generation, project)) return result
      location.value = result.location ?? null
      await listMessages(id)
      if (!isCurrentConversation(id, generation, project)) return result
      await loadRun(result.data.agent_run_id)
      return result
    } finally { sendingMessage = false }
  }

  async function getPredictionResult(id: string) {
    if (!projectId.value || accessContext.value?.project_selection !== 'resolved') throw new Error('当前项目访问范围未解析，无法回查预测结果')
    if (!SAFE_PREDICTION_ID.test(id)) throw new Error('预测结果引用 ID 不合法')
    const requestProjectId = projectId.value
    const requestConversationId = selectedConversationId.value
    if (!requestConversationId) throw new Error('当前没有已选择的助手会话，无法回查预测结果')
    const previousResults = { ...predictionResults.value }; delete previousResults[id]
    predictionResults.value = previousResults
    const result = await apiClient.requestWithMeta<ContractPrediction>(`/predictions/${encodeURIComponent(id)}`)
    if (projectId.value !== requestProjectId || selectedConversationId.value !== requestConversationId || result.data.project_id !== requestProjectId || result.data.id !== id) throw new Error('服务端返回的预测结果与当前项目引用不匹配')
    predictionResults.value = { ...predictionResults.value, [id]: result.data }
    return result
  }

  async function listPriorityTools(): Promise<ContractPriorityTool[]> {
    priorityToolsState.value = 'loading'; priorityToolsError.value = null; priorityTools.value = []
    try {
      const result = await apiClient.getRaw<ContractPriorityTools>('/assistant/priority/tools')
      if (!result || typeof result.request_id !== 'string' || !Array.isArray(result.tools)) throw new Error('能力目录响应格式无效')
      priorityTools.value = result.tools
      priorityToolsState.value = 'ready'
      return result.tools
    } catch (reason) {
      priorityTools.value = []
      priorityToolsState.value = 'error'
      priorityToolsError.value = reason instanceof ApiClientError
        ? `${reason.code}: ${reason.message}`
        : '能力目录请求失败'
      return []
    }
  }

  async function listDataDomains(cursor?: string): Promise<ApiResult<ContractDataDomain[]>> {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
    return apiClient.requestWithMeta<ContractDataDomain[]>(`/data-domains${query}`)
  }

  async function listPredictionTypes(): Promise<ContractTypeSummary[]> {
    return apiClient.get<ContractTypeSummary[]>('/prediction/types')
  }

  function resolvedSummaryProjectId(): string {
    if (!projectId.value || accessContext.value?.project_selection !== 'resolved') throw new Error('当前项目访问范围未解析，无法查询项目摘要')
    return projectId.value
  }

  async function getDataVersionSummary(domainId: string): Promise<ContractDataVersionSummary> {
    if (!domainId) throw new Error('请先选择数据域')
    const requestProjectId = resolvedSummaryProjectId()
    const summary = await apiClient.get<ContractDataVersionSummary>(`/assistant/priority/data-version-summary?project_id=${encodeURIComponent(requestProjectId)}&domain_id=${encodeURIComponent(domainId)}`)
    if (projectId.value !== requestProjectId || accessContext.value?.project_selection !== 'resolved') throw new Error('当前项目已切换，数据版本摘要已失效')
    return summary
  }

  async function getPredictionRecordSummary(chromatographyType?: string): Promise<ContractPredictionRecordSummary> {
    const requestProjectId = resolvedSummaryProjectId()
    const query = new URLSearchParams({ project_id: requestProjectId })
    if (chromatographyType) query.set('chromatography_type', chromatographyType)
    const summary = await apiClient.get<ContractPredictionRecordSummary>(`/assistant/priority/prediction-record-summary?${query.toString()}`)
    if (projectId.value !== requestProjectId || accessContext.value?.project_selection !== 'resolved') throw new Error('当前项目已切换，预测记录摘要已失效')
    return summary
  }

  async function createRegistrationDraft(body: ContractPriorityRegistrationDraftRequest): Promise<ContractAssistantModelRegistrationDraft> {
    return apiClient.post<ContractAssistantModelRegistrationDraft>('/assistant/priority/model-registration-drafts', body, { idempotencyKey: createIdempotencyKey() })
  }

  async function getRegistrationDraft(resourceId: string): Promise<ContractAssistantModelRegistrationDraft> {
    if (!projectId.value || accessContext.value?.project_selection !== 'resolved') throw new Error('当前项目访问范围未解析，无法回查登记草稿')
    if (!SAFE_REGISTRATION_ID.test(resourceId)) throw new Error('登记草稿引用 ID 不合法')
    const requestProjectId = projectId.value
    const previousDrafts = { ...registrationDrafts.value }; delete previousDrafts[resourceId]
    const previousErrors = { ...registrationDraftErrors.value }; delete previousErrors[resourceId]
    registrationDrafts.value = previousDrafts
    registrationDraftErrors.value = previousErrors
    registrationDraftStatus.value = { ...registrationDraftStatus.value, [resourceId]: 'loading' }
    try {
      const draft = await apiClient.get<ContractAssistantModelRegistrationDraft>(assistantRegistrationDraftPath(resourceId, requestProjectId))
      if (projectId.value !== requestProjectId || draft.project_id !== requestProjectId || draft.id !== resourceId) throw new Error('服务端返回的登记草稿与当前项目引用不匹配')
      registrationDrafts.value = { ...registrationDrafts.value, [resourceId]: draft }
      registrationDraftStatus.value = { ...registrationDraftStatus.value, [resourceId]: 'ready' }
      return draft
    } catch (reason) {
      if (projectId.value === requestProjectId) {
        const drafts = { ...registrationDrafts.value }; delete drafts[resourceId]
        registrationDrafts.value = drafts
        registrationDraftStatus.value = { ...registrationDraftStatus.value, [resourceId]: 'error' }
        registrationDraftErrors.value = { ...registrationDraftErrors.value, [resourceId]: reason instanceof ApiClientError ? `${reason.code}: ${reason.message}` : reason instanceof Error ? reason.message : '登记草稿回查失败' }
      }
      throw reason
    }
  }

  async function listEvents(id: string, afterSeq?: string, signal?: AbortSignal) {
    const generation = runGeneration
    const conversation = conversationGeneration
    const result = await apiClient.requestWithMeta<ContractAssistantEventPage>(`/agent-runs/${encodeURIComponent(id)}/events${afterSeq ? `?after_seq=${encodeURIComponent(afterSeq)}` : ''}`, { signal })
    if (isCurrentRun(id, generation, conversation)) events.value = result.data
    return result
  }

  function closeEventTransport() {
    streamGeneration += 1
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
    if (restoreTimer) { clearTimeout(restoreTimer); restoreTimer = null }
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null }
    refreshQueued = false; refreshQueuedImmediate = false; refreshNextRequest = null
    restoreController?.abort(); restoreController = null
    stream?.close(); stream = null
  }

  function stopEventStream() {
    runGeneration += 1
    closeEventTransport()
    activeRunId = null
    streamTextProjections.value = streamTextProjections.value.map((item) => ({ ...item, incomplete: true }))
  }

  function projectionKey(conversationId: string, runId: string, messageId: string, blockIndex: number) {
    return `${conversationId}\u0000${runId}\u0000${messageId}\u0000${blockIndex}`
  }

  function requireMessageSnapshot(conversationId: string, runId: string, messageId: string, blockIndex: number) {
    const key = projectionKey(conversationId, runId, messageId, blockIndex)
    snapshotRecoveryEpoch += 1
    snapshotRecovery.set(key, { conversation_id: conversationId, run_id: runId, message_id: messageId, block_index: blockIndex, token: snapshotRecoveryEpoch })
  }

  function requireKnownMessageSnapshots(conversationId: string, runId: string, messageId: string) {
    const indexes = new Set<number>()
    for (const item of streamTextProjections.value) {
      if (item.conversation_id === conversationId && item.run_id === runId && item.message_id === messageId) indexes.add(item.block_index)
    }
    const message = messages.value.find((item) => item.id === messageId && item.role === 'assistant')
    for (const [index, block] of (message?.blocks ?? []).entries()) if (block.type === 'text') indexes.add(index)
    for (const index of indexes) requireMessageSnapshot(conversationId, runId, messageId, index)
  }

  function applyMessageDelta(id: string, conversationId: string, data: Record<string, unknown>) {
    const messageId = data.message_id
    const blockIndex = data.block_index
    const delta = data.delta
    const offset = data.offset
    if (typeof messageId !== 'string' || typeof blockIndex !== 'number' || typeof delta !== 'string' || typeof offset !== 'number') {
      if (typeof messageId === 'string' && messageId.length > 0 && typeof blockIndex === 'number' && Number.isSafeInteger(blockIndex) && blockIndex >= 0) requireMessageSnapshot(conversationId, id, messageId, blockIndex)
      void requestRunDetails(id, runGeneration, conversationGeneration, true).catch(() => undefined)
      return
    }
    const key = projectionKey(conversationId, id, messageId, blockIndex)
    let current = streamTextProjections.value.find((item) => projectionKey(item.conversation_id, item.run_id, item.message_id, item.block_index) === key)
    if (!current && Number.isSafeInteger(blockIndex) && blockIndex >= 0) {
      const canonicalMessage = messages.value.find((item) => item.id === messageId && item.role === 'assistant')
      const canonicalBlock = canonicalMessage?.blocks?.[blockIndex]
      if (canonicalBlock?.type === 'text') current = { ...assistantTextProjectionFromSnapshot(messageId, blockIndex, canonicalBlock.text), conversation_id: conversationId, run_id: id, incomplete: true }
    }
    const result = applyAssistantTextDelta(current ?? null, { message_id: messageId, block_index: blockIndex, delta, offset })
    if (result.accepted && result.projection) {
      const next: ScopedAssistantTextProjection = { ...result.projection, conversation_id: conversationId, run_id: id, incomplete: true }
      const stored = streamTextProjections.value.some((item) => projectionKey(item.conversation_id, item.run_id, item.message_id, item.block_index) === key)
      streamTextProjections.value = stored
        ? streamTextProjections.value.map((item) => projectionKey(item.conversation_id, item.run_id, item.message_id, item.block_index) === key ? next : item)
        : [...streamTextProjections.value, next]
    }
    if (result.needsSnapshot) {
      if (current && !streamTextProjections.value.some((item) => projectionKey(item.conversation_id, item.run_id, item.message_id, item.block_index) === key)) streamTextProjections.value = [...streamTextProjections.value, current]
      if (messageId.length > 0 && Number.isSafeInteger(blockIndex) && blockIndex >= 0) requireMessageSnapshot(conversationId, id, messageId, blockIndex)
      void requestRunDetails(id, runGeneration, conversationGeneration, true).catch(() => undefined)
    }
  }

  function requestRunDetails(id: string, runAtStart: number, conversationAtStart: number, immediate = false): Promise<void> {
    if (!isCurrentRun(id, runAtStart, conversationAtStart)) return Promise.resolve()
    const scope = `${id}:${runAtStart}:${conversationAtStart}`
    if (refreshFlight) {
      if (refreshFlightScope === scope) {
        refreshQueued = true
        refreshQueuedImmediate ||= immediate
      } else {
        refreshNextRequest = { id, runAtStart, conversationAtStart, immediate }
      }
      return refreshFlight
    }
    if (refreshTimer) {
      if (!immediate) return Promise.resolve()
      clearTimeout(refreshTimer); refreshTimer = null
    }
    const execute = (): Promise<void> => {
      if (!isCurrentRun(id, runAtStart, conversationAtStart)) return Promise.resolve()
      const conversationId = run.value?.conversation_id ?? selectedConversationId.value ?? undefined
      const flight = refreshRunDetails(id, runAtStart, conversationAtStart, conversationId)
      refreshFlight = flight
      refreshFlightScope = scope
      return flight.finally(() => {
        if (refreshFlight === flight) { refreshFlight = null; refreshFlightScope = null }
        const nextRequest = refreshNextRequest
        if (nextRequest && isCurrentRun(nextRequest.id, nextRequest.runAtStart, nextRequest.conversationAtStart)) {
          refreshNextRequest = null
          void requestRunDetails(nextRequest.id, nextRequest.runAtStart, nextRequest.conversationAtStart, nextRequest.immediate).catch(() => undefined)
          return
        }
        if (refreshQueued && isCurrentRun(id, runAtStart, conversationAtStart)) {
          const nextImmediate = refreshQueuedImmediate
          refreshQueued = false; refreshQueuedImmediate = false
          void requestRunDetails(id, runAtStart, conversationAtStart, nextImmediate).catch(() => undefined)
        }
      })
    }
    if (immediate) return execute()
    refreshTimer = setTimeout(() => {
      refreshTimer = null
      void execute().catch(() => undefined)
    }, 100)
    return Promise.resolve()
  }

  function startEventStream(id: string, expectedRunGeneration?: number, expectedConversationGeneration = conversationGeneration) {
    if (expectedRunGeneration !== undefined && !isCurrentRun(id, expectedRunGeneration, expectedConversationGeneration)) return
    resetEventTrackingForRun(id)
    closeEventTransport()
    if (expectedRunGeneration === undefined) { runGeneration += 1; activeRunId = id }
    const runAtStart = runGeneration
    const conversationAtStart = expectedConversationGeneration
    const generation = streamGeneration
    if (activeRunId !== id) { seenSeq.clear(); liveEvents.value = []; lastEventSeq.value = ''; activeRunId = id }
    error.value = null
    stream = createEventStream(`/agent-runs/${encodeURIComponent(id)}/events/stream`, {
      afterSeq: lastEventSeq.value || undefined, lastEventId: lastEventSeq.value || undefined,
      onOpen: () => { if (generation === streamGeneration && isCurrentRun(id, runAtStart, conversationAtStart)) state.value = 'ready' },
      onEvent: (event: StreamEvent) => {
        if (generation !== streamGeneration || !isCurrentRun(id, runAtStart, conversationAtStart)) return
        const seq = event.seq || ''; if (seq && seenSeq.has(seq)) return; if (seq) { seenSeq.add(seq); lastEventSeq.value = seq }
        liveEvents.value.push(event as ContractAssistantEvent)
        const data = event.data as Record<string, unknown>
        const conversationId = run.value?.conversation_id ?? selectedConversationId.value
        if (event.type === 'message.delta' && conversationId) {
          applyMessageDelta(id, conversationId, data)
          return
        }
        if (event.type === 'message.completed') {
          if (conversationId && typeof data.message_id === 'string' && data.message_id.length > 0) requireKnownMessageSnapshots(conversationId, id, data.message_id)
          void requestRunDetails(id, runAtStart, conversationAtStart, true).catch(() => undefined)
          return
        }
        if (event.type === 'warning' && data.code === MESSAGE_SNAPSHOT_REPLACED) {
          if (conversationId && typeof data.message_id === 'string' && data.message_id.length > 0) {
            if (typeof data.block_index === 'number' && Number.isSafeInteger(data.block_index) && data.block_index >= 0) requireMessageSnapshot(conversationId, id, data.message_id, data.block_index)
            else requireKnownMessageSnapshots(conversationId, id, data.message_id)
          }
          void requestRunDetails(id, runAtStart, conversationAtStart, true).catch(() => undefined)
          return
        }
        if (TERMINAL_RUN_EVENTS.has(event.type)) {
          if (conversationId) {
            for (const item of streamTextProjections.value) if (item.conversation_id === conversationId && item.run_id === id) requireMessageSnapshot(conversationId, id, item.message_id, item.block_index)
          }
          closeEventTransport()
          void requestRunDetails(id, runAtStart, conversationAtStart, true).catch(() => {
            if (isCurrentRun(id, runAtStart, conversationAtStart)) error.value = '助手最终状态读取失败'
          })
          return
        }
        if (event.type === 'llm.started' || /^(run\.|message\.|tool\.)/.test(event.type)) {
          void requestRunDetails(id, runAtStart, conversationAtStart).catch(() => undefined)
        }
      },
      onError: (reason) => {
        if (generation !== streamGeneration || !isCurrentRun(id, runAtStart, conversationAtStart)) return
        stream = null
        if (reason instanceof EventStreamError && reason.status === 410) {
          closeEventTransport()
          error.value = '事件重放窗口已过期，正在通过普通详情恢复'
          void pollRunDetails(id, runAtStart, conversationAtStart)
        } else if (!TERMINAL_RUNS.has(run.value?.status ?? '') && reason instanceof Error) {
          error.value = reason.message
          reconnectTimer = setTimeout(() => {
            if (generation === streamGeneration && isCurrentRun(id, runAtStart, conversationAtStart) && !TERMINAL_RUNS.has(run.value?.status ?? '')) startEventStream(id, runAtStart, conversationAtStart)
          }, 3000)
        }
      },
    })
  }

  async function refreshRunDetails(id: string, runAtStart: number, conversationAtStart: number, conversationId = run.value?.conversation_id ?? selectedConversationId.value ?? undefined, signal?: AbortSignal) {
    if (!conversationId || !isCurrentRun(id, runAtStart, conversationAtStart)) return
    await Promise.all([getRun(id, signal), listToolCalls(id, signal), listMessages(conversationId, undefined, signal, { id, generation: runAtStart, conversationGeneration: conversationAtStart })])
  }

  async function pollRunDetails(id: string, runAtStart: number, conversationAtStart: number) {
    if (!isCurrentRun(id, runAtStart, conversationAtStart)) return
    const controller = new AbortController()
    restoreController = controller
    try {
      if (refreshFlight) await refreshFlight
      if (controller.signal.aborted || !isCurrentRun(id, runAtStart, conversationAtStart)) return
      await refreshRunDetails(id, runAtStart, conversationAtStart, run.value?.conversation_id, controller.signal)
    } catch (reason) {
      if (controller.signal.aborted || !isCurrentRun(id, runAtStart, conversationAtStart)) return
      error.value = '助手详情恢复失败'
    } finally {
      if (restoreController === controller) restoreController = null
    }
    if (!controller.signal.aborted && isCurrentRun(id, runAtStart, conversationAtStart) && !TERMINAL_RUNS.has(run.value?.status ?? '')) {
      restoreTimer = setTimeout(() => { restoreTimer = null; void pollRunDetails(id, runAtStart, conversationAtStart) }, 2000)
    }
  }

  async function listToolCalls(id: string, signal?: AbortSignal) {
    const generation = runGeneration
    const conversation = conversationGeneration
    const result = await apiClient.requestWithMeta<ContractToolCall[]>(`/agent-runs/${encodeURIComponent(id)}/tool-calls`, { signal })
    if (isCurrentRun(id, generation, conversation)) toolCalls.value = result.data
    return result
  }

  async function resumeRun(id: string, body: ContractResumeBody, etag: string): Promise<ApiResult<ContractAgentRun>> {
    if (!prepareRunAction(id)) throw new Error('当前助手 Run 已切换')
    const generation = runGeneration
    const conversation = conversationGeneration
    const result = await apiClient.requestWithMeta<ContractAgentRun>(`/agent-runs/${encodeURIComponent(id)}/resume`, { method: 'POST', body, ifMatch: etag, idempotencyKey: createIdempotencyKey() })
    if (!isCurrentRun(id, generation, conversation)) return result
    run.value = result.data; location.value = result.location ?? null
    const conversationId = result.data.conversation_id || selectedConversationId.value
    if (conversationId) {
      await listMessages(conversationId)
      if (isCurrentRun(id, generation, conversation)) await loadRun(id)
    }
    return result
  }

  async function confirmRun(id: string, binding: NonNullable<ContractAgentRun['pending_actions']>[number], etag: string): Promise<void> {
    if (!prepareRunAction(id)) throw new Error('当前助手 Run 已切换')
    const generation = runGeneration
    const conversation = conversationGeneration
    let confirmationId = binding.confirmation_id
    if (confirmationId) {
      const result = await apiClient.requestWithMeta<ContractConfirmation>(`/confirmations/${encodeURIComponent(confirmationId)}`)
      if (!isCurrentRun(id, generation, conversation)) return
      const confirmation = result.data
      const project = projectId.value ?? run.value?.project_id
      const sameResource = confirmation.resource_ref.type === binding.resource_ref.type && confirmation.resource_ref.id === binding.resource_ref.id
      const sameBinding = confirmation.id === confirmationId && Boolean(project) && confirmation.project_id === project && sameResource && confirmation.action === binding.action && confirmation.revision === binding.revision && confirmation.config_hash === binding.config_hash
      if (!sameBinding) throw new Error('服务端确认对象与当前助手动作不匹配')
      if (confirmation.status === 'CONSUMED') {
        await loadRun(id)
        if (conversation !== conversationGeneration || activeRunId !== id) return
        const conversationId = run.value?.conversation_id ?? selectedConversationId.value
        if (conversationId) {
          await listMessages(conversationId)
          if (conversation !== conversationGeneration || activeRunId !== id) return
        }
        confirmationNotice.value = '已完成/已恢复'; confirmationNoticeRunId.value = id
        return
      }
      const usable = confirmation.status === 'CONFIRMED' && Number.isFinite(Date.parse(confirmation.expires_at)) && Date.parse(confirmation.expires_at) > Date.now()
      if (!usable) throw new Error('服务端确认对象已失效')
    } else {
      const confirmations = useConfirmationStore()
      await confirmations.loadPreview(binding.resource_ref, binding.action)
      if (!isCurrentRun(id, generation, conversation)) return
      if (!confirmations.preview?.can_confirm) throw new Error('当前业务动作未通过服务端确认预览')
      confirmationId = (await confirmations.create()).id
    }
    if (!isCurrentRun(id, generation, conversation)) return
    confirmationNotice.value = null; confirmationNoticeRunId.value = null
    await resumeRun(id, makeResumeBody('confirmation', confirmationId), etag)
  }

  async function cancelRun(id: string, etag: string, reason?: string): Promise<ApiResult<ContractAgentRun>> {
    if (!prepareRunAction(id)) throw new Error('当前助手 Run 已切换')
    const generation = runGeneration
    const conversation = conversationGeneration
    const body: ContractCancelAgentRunBody = reason ? { reason } : {}
    const result = await apiClient.requestWithMeta<ContractAgentRun>(`/agent-runs/${encodeURIComponent(id)}/cancel`, { method: 'POST', body, ifMatch: etag, idempotencyKey: createIdempotencyKey() })
    if (!isCurrentRun(id, generation, conversation)) return result
    run.value = result.data; location.value = result.location ?? null
    const conversationId = result.data.conversation_id || selectedConversationId.value
    if (conversationId) await listMessages(conversationId)
    if (isCurrentRun(id, generation, conversation)) {
      if (TERMINAL_RUNS.has(run.value?.status ?? '')) stopEventStream()
      else startEventStream(id, generation, conversation)
    }
    return result
  }

  return { accessContext, projectId, selectedConversationId, run, conversations, searchResults, archivedConversations, messages, streamTextProjections, events, liveEvents, lastEventSeq, toolCalls, predictionResults, registrationDrafts, registrationDraftStatus, registrationDraftErrors, priorityTools, priorityToolsState, priorityToolsError, confirmationNotice, confirmationNoticeRunId, state, error, location, bootstrap, getAccessContext, listConversations, renameConversation, archiveConversation, deleteConversation, createConversation, loadConversation, listMessages, listConversationRuns, getRun, loadRun, sendMessage, getPredictionResult, listPriorityTools, listDataDomains, listPredictionTypes, getDataVersionSummary, getPredictionRecordSummary, createRegistrationDraft, getRegistrationDraft, listEvents, startEventStream, stopEventStream, listToolCalls, resumeRun, confirmRun, cancelRun }
})
