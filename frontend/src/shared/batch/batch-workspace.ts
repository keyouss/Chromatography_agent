export type BatchWorkspaceType = 'tlc' | 'cc' | 'hplc' | 'gc' | 'sfc'

export interface BatchWorkspace {
  version: 1
  actorId: string
  projectId: string
  type: BatchWorkspaceType
  tool: 'batch'
  formDraft?: unknown
  draftId?: string
  acceptedId?: string
  artifactId?: string
  submissionLocked?: boolean
}

const memory = new Map<string, BatchWorkspace>()

export function batchWorkspaceKey(actorId: string, projectId: string, type: BatchWorkspaceType, tool: 'batch'): string {
  return `chromatography.batch.v1:${encodeURIComponent(actorId)}:${encodeURIComponent(projectId)}:${type}:${tool}`
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function validWorkspace(value: unknown, key: string): BatchWorkspace | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const input = value as Partial<BatchWorkspace>
  if (input.version !== 1 || input.tool !== 'batch' || !input.actorId || !input.projectId || !input.type) return null
  if (!['tlc', 'cc', 'hplc', 'gc', 'sfc'].includes(input.type)) return null
  if (batchWorkspaceKey(input.actorId, input.projectId, input.type, 'batch') !== key) return null
  const workspace: BatchWorkspace = {
    version: 1,
    actorId: input.actorId,
    projectId: input.projectId,
    type: input.type,
    tool: 'batch',
  }
  if ('formDraft' in input) workspace.formDraft = input.formDraft
  if (typeof input.draftId === 'string') workspace.draftId = input.draftId
  if (typeof input.acceptedId === 'string') workspace.acceptedId = input.acceptedId
  if (typeof input.artifactId === 'string') workspace.artifactId = input.artifactId
  if (typeof input.submissionLocked === 'boolean') workspace.submissionLocked = input.submissionLocked
  return workspace
}

export function readBatchWorkspace(key: string): BatchWorkspace | null {
  try {
    const stored = localStorage.getItem(key)
    if (stored !== null) {
      const workspace = validWorkspace(JSON.parse(stored) as unknown, key)
      if (workspace) return clone(workspace)
    }
  } catch { /* local storage is optional */ }
  const fallback = memory.get(key)
  return fallback ? clone(fallback) : null
}

export function writeBatchWorkspace(key: string, value: BatchWorkspace | null): void {
  if (value === null) {
    memory.delete(key)
    try { localStorage.removeItem(key) } catch { /* local storage is optional */ }
    return
  }
  const workspace = validWorkspace(value, key)
  if (!workspace) throw new Error('批量工作区数据与其 actor/project/type/tool 键不匹配。')
  const copy = clone(workspace)
  memory.set(key, copy)
  try { localStorage.setItem(key, JSON.stringify(copy)) } catch { /* retain the in-memory workspace */ }
}
