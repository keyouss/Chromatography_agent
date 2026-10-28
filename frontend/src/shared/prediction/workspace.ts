import type { ContractPredictionAccepted } from '@/core/contract-types'

export interface PredictionWorkspace {
  version: 1
  actor: string
  project: string
  type: string
  tool: string
  smiles: string[]
  form: unknown
  modelKey: string
  submissionLocked: boolean
  predictionId: string | null
  accepted: ContractPredictionAccepted | null
}

const PREFIX = 'chrom-platform.prediction-workspace-v1'
const memory = new Map<string, PredictionWorkspace>()

export function predictionWorkspaceKey(actor: string, project: string, type: string, tool: string): string {
  return [PREFIX, actor, project, type, tool].map(encodeURIComponent).join(':')
}

function isAccepted(value: unknown): value is ContractPredictionAccepted {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const accepted = value as Record<string, unknown>
  return typeof accepted.id === 'string' && accepted.id.length > 0
    && typeof accepted.job_id === 'string' && accepted.job_id.length > 0
    && typeof accepted.status === 'string'
}

function isWorkspace(value: unknown, key: string): value is PredictionWorkspace {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const item = value as Record<string, unknown>
  return item.version === 1 && Array.isArray(item.smiles) && item.smiles.length <= 8
    && item.smiles.every((smiles) => typeof smiles === 'string')
    && typeof item.actor === 'string' && typeof item.project === 'string'
    && typeof item.type === 'string' && typeof item.tool === 'string'
    && typeof item.modelKey === 'string'
    && typeof item.submissionLocked === 'boolean'
    && (item.predictionId === null || typeof item.predictionId === 'string')
    && (item.accepted === null || isAccepted(item.accepted))
    && key === predictionWorkspaceKey(item.actor, item.project, item.type, item.tool)
}

export function readPredictionWorkspace(key: string): PredictionWorkspace | null {
  try {
    const raw = window.localStorage.getItem(key)
    if (raw && raw.length < 65536) {
      const value: unknown = JSON.parse(raw)
      if (isWorkspace(value, key)) {
        memory.set(key, value)
        return value
      }
    }
  } catch { /* Storage can be blocked; keep using the in-page copy. */ }
  return memory.get(key) ?? null
}

export function writePredictionWorkspace(key: string, value: PredictionWorkspace): void {
  const snapshot = JSON.parse(JSON.stringify(value)) as PredictionWorkspace
  if (!isWorkspace(snapshot, key)) throw new Error('Prediction workspace identity or payload is invalid.')
  memory.set(key, snapshot)
  try { window.localStorage.setItem(key, JSON.stringify(snapshot)) } catch { /* Keep the in-memory snapshot. */ }
}

export function emptyPredictionWorkspace(actor: string, project: string, type: string, tool: string): PredictionWorkspace {
  return { version: 1, actor, project, type, tool, smiles: [], form: null, modelKey: '', submissionLocked: false, predictionId: null, accepted: null }
}
