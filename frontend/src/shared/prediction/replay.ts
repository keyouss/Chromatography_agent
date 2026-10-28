import { apiClient, createIdempotencyKey } from '@/core/api/client'
import { exactModelIdentityMatches, type PredictionCandidateDraftView } from './model-selection'

export type ReplayInput = Record<string, unknown>
export type ReplayDraft = PredictionCandidateDraftView & {
  /** The contract permits omitting refs; replay editing needs them to bind snapshots. */
  structure_refs: NonNullable<PredictionCandidateDraftView['structure_refs']>
}

type DraftPointer = { project_id: string; draft_id: string }
export class ReplayIdentityError extends Error {}
const STORAGE_KEY = 'chrom-platform.replay-draft'
const pendingKeys = new Map<string, string>()
const REPLAY_DRAFT_PROPERTIES = new Set<keyof PredictionCandidateDraftView>([
  'chromatography_type', 'created_at', 'etag', 'id', 'input', 'model_selection',
  'modifier_structure_snapshots', 'project_id', 'revision', 'schema_version',
  'source_model_snapshot', 'source_prediction_id', 'source_record_id',
  'source_record_revision', 'structure_refs', 'structure_snapshots', 'tool', 'updated_at',
])

function pointers(): Record<string, string> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}')
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, string> : {}
  } catch { return {} }
}

export function readReplayPointer(projectId: string): DraftPointer | null {
  const draftId = pointers()[projectId]
  return typeof draftId === 'string' && draftId ? { project_id: projectId, draft_id: draftId } : null
}

export function saveReplayPointer(pointer: DraftPointer): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...pointers(), [pointer.project_id]: pointer.draft_id })) } catch { /* storage may be unavailable */ }
}

export function clearReplayPointer(pointer: DraftPointer): void {
  try {
    const stored = pointers()
    if (stored[pointer.project_id] !== pointer.draft_id) return
    delete stored[pointer.project_id]
    localStorage.setItem(STORAGE_KEY, JSON.stringify(stored))
  } catch { /* storage may be unavailable */ }
}

function parseReplayDraft(value: unknown): ReplayDraft {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('服务端返回的重放草稿格式无效')
  const draft = value as Record<string, unknown>
  if (Object.keys(draft).some((key) => !REPLAY_DRAFT_PROPERTIES.has(key as keyof PredictionCandidateDraftView))) {
    throw new Error('服务端返回的重放草稿包含合同外字段')
  }
  const selection = draft.model_selection
  const sourceModel = draft.source_model_snapshot
  const isNonEmptyString = (item: unknown): item is string => typeof item === 'string' && item.trim().length > 0
  const isSnapshot = (item: unknown): boolean => Boolean(item && typeof item === 'object' && !Array.isArray(item)
    && isNonEmptyString((item as Record<string, unknown>).id)
    && isNonEmptyString((item as Record<string, unknown>).project_id)
    && isNonEmptyString((item as Record<string, unknown>).canonical_smiles)
    && ['specified', 'unspecified', 'not_applicable'].includes(String((item as Record<string, unknown>).stereochemistry_status)))
  const refs = draft.structure_refs
  if (!isNonEmptyString(draft.id) || !isNonEmptyString(draft.project_id) || !isNonEmptyString(draft.source_record_id)
    || !isNonEmptyString(draft.source_prediction_id) || !['tlc', 'cc', 'hplc', 'gc', 'sfc', 'chiral_lc'].includes(String(draft.chromatography_type))
    || !isNonEmptyString(draft.tool) || !isNonEmptyString(draft.schema_version)
    || !draft.input || typeof draft.input !== 'object' || Array.isArray(draft.input)
    || !Array.isArray(refs) || !refs.length || !refs.every((item) => item && typeof item === 'object' && !Array.isArray(item) && (item as Record<string, unknown>).type === 'structure' && isNonEmptyString((item as Record<string, unknown>).id))
    || !Array.isArray(draft.structure_snapshots) || !draft.structure_snapshots.every(isSnapshot)
    || (draft.modifier_structure_snapshots !== undefined && (!Array.isArray(draft.modifier_structure_snapshots) || !draft.modifier_structure_snapshots.every(isSnapshot)))
    || !selection || typeof selection !== 'object' || Array.isArray(selection) || (selection as Record<string, unknown>).mode !== 'exact'
    || !isNonEmptyString((selection as Record<string, unknown>).model_id) || !isNonEmptyString((selection as Record<string, unknown>).model_version)
    || !sourceModel || typeof sourceModel !== 'object' || Array.isArray(sourceModel)
    || !['id', 'version', 'business_scope_id', 'artifact_sha256', 'input_schema_version', 'output_schema_version'].every((key) => isNonEmptyString((sourceModel as Record<string, unknown>)[key]))
    || !exactModelIdentityMatches(
      selection as PredictionCandidateDraftView['model_selection'],
      sourceModel as PredictionCandidateDraftView['source_model_snapshot'],
    )
    || !Number.isInteger(draft.revision) || Number(draft.revision) < 1
    || !Number.isInteger(draft.source_record_revision) || Number(draft.source_record_revision) < 1
    || !isNonEmptyString(draft.etag) || !isNonEmptyString(draft.created_at) || Number.isNaN(Date.parse(String(draft.created_at)))
    || !isNonEmptyString(draft.updated_at) || Number.isNaN(Date.parse(String(draft.updated_at)))) {
    throw new Error('服务端返回的重放草稿缺少必要字段')
  }
  const structureRefs = refs as Record<string, unknown>[]
  const structureSnapshots = draft.structure_snapshots as Record<string, unknown>[]
  if (structureSnapshots.length !== structureRefs.length || structureSnapshots.some((item, index) => item.project_id !== draft.project_id || item.id !== structureRefs[index]?.id)) {
    throw new Error('重放草稿结构快照与结构引用或项目不匹配')
  }
  const input = draft.input as Record<string, unknown>
  const modifierComponents = input.modifier_components
  const modifierSnapshots = draft.modifier_structure_snapshots
  if (Array.isArray(modifierComponents)) {
    if (!Array.isArray(modifierSnapshots) || modifierSnapshots.length !== modifierComponents.length
      || modifierSnapshots.some((item, index) => item.project_id !== draft.project_id || item.id !== (modifierComponents[index] as Record<string, unknown> | null)?.structure_ref)) {
      throw new Error('重放草稿 Modifier 快照与结构引用或项目不匹配')
    }
  } else if (modifierSnapshots !== undefined && (!Array.isArray(modifierSnapshots) || modifierSnapshots.length !== 0)) {
    throw new Error('重放草稿包含未绑定的 Modifier 快照')
  }
  return draft as unknown as ReplayDraft
}

export async function createReplayDraft(recordId: string, projectId: string): Promise<ReplayDraft> {
  const pendingKey = `${projectId}:${recordId}`
  const idempotencyKey = pendingKeys.get(pendingKey) ?? createIdempotencyKey()
  pendingKeys.set(pendingKey, idempotencyKey)
  const result = await apiClient.requestWithMeta<unknown>(`/prediction-records/${encodeURIComponent(recordId)}/replay-draft`, {
    method: 'POST', body: {}, idempotencyKey,
  })
  const draft = parseReplayDraft(result.data)
  if (draft.project_id !== projectId || draft.source_record_id !== recordId) throw new Error('重放草稿与当前项目或来源记录不匹配')
  saveReplayPointer({ project_id: projectId, draft_id: draft.id })
  pendingKeys.delete(pendingKey)
  return draft
}

export async function getReplayDraft(pointer: DraftPointer): Promise<ReplayDraft> {
  const result = await apiClient.request<unknown>(`/prediction-drafts/${encodeURIComponent(pointer.draft_id)}?project_id=${encodeURIComponent(pointer.project_id)}`)
  const draft = parseReplayDraft(result)
  if (draft.id !== pointer.draft_id || draft.project_id !== pointer.project_id) throw new ReplayIdentityError('草稿不属于当前项目或身份不匹配')
  return draft
}
