/**
 * SFC form types mirror the frozen v1.1 request names.  They deliberately do
 * not include transport state, response fields, or browser event objects.
 */

export const SFC_CANDIDATE_KEYS = [
  'column',
  'stationary_phase',
  'mode',
  'length_mm',
  'inner_diameter_mm',
  'particle_size_um',
  'column_temperature_c',
  'flow_rate_ml_min',
] as const

export const SFC_PINNED_REFERENCE = {
  column: 'AD',
  stationary_phase: 'amylose tris (3,5-dimethylphenylcarbamate)',
  mode: 'coated',
  length_mm: 150,
  inner_diameter_mm: 4.6,
  particle_size_um: 5,
  column_temperature_c: 35,
  flow_rate_ml_min: 2.5,
  modifier_smiles: 'CO',
  modifier_total_fraction: 0.2,
} as const

export type SfcPinnedCondition = typeof SFC_PINNED_REFERENCE

export function isPinnedSfcCondition(value: unknown): value is SfcPinnedCondition {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const condition = value as Record<string, unknown>
  return Object.entries(SFC_PINNED_REFERENCE).every(([key, expected]) => condition[key] === expected)
}

export type SfcCandidateKey = (typeof SFC_CANDIDATE_KEYS)[number]
export type SfcScope = 'all_column' | 'single_column' | 'batch'
export function isSfcCandidateKey(value: string): value is SfcCandidateKey {
  return (SFC_CANDIDATE_KEYS as readonly string[]).includes(value)
}

export interface SfcConditionOption {
  value: string
  count?: number
}

export interface SfcConditionField {
  key: SfcCandidateKey
  label: string
  unit: string | null
  type: 'fixed_multi' | 'numeric_multi_custom'
  options: readonly SfcConditionOption[]
  defaults?: readonly string[]
}

export interface SfcModifierOption {
  name: string
  smiles: string
}

/** Shape returned by the v1.1 /api/conditions/options endpoint. */
export interface SfcServerOptions {
  fields: readonly SfcConditionField[]
  common_modifiers?: readonly SfcModifierOption[]
  defaults?: Partial<Record<SfcCandidateKey, readonly string[]>>
  max_candidates?: number
}

export interface SfcModelOption {
  id: string
  threshold: string
  available?: boolean
  unavailable_reason?: string
  recommended?: boolean
  scope?: 'all_column' | 'single_column'
  single_column_group?: string
}

export interface SfcSingleColumnGroup {
  key: string
  slug?: string
  stationary_phase: string
  mode: string
  allowed_columns: readonly string[]
}

/** The model/limit subset needed by this presentational form. */
export interface SfcServerCapability {
  models: readonly SfcModelOption[]
  thresholds?: readonly string[]
  single_column_groups?: readonly SfcSingleColumnGroup[]
  limits?: { max_candidates?: number; max_rows?: number; max_evaluations?: number }
}

export interface SfcModifier {
  name: string
  smiles: string
  ratio: number
}

export type SfcCandidateSpace = Record<SfcCandidateKey, string[]>

/** Runtime guard for the contract's 1–8 supported candidate axes. */
export function isValidSfcCandidateAxes(value: unknown): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const axes = value as Record<string, unknown>
  const keys = Object.keys(axes)
  if (keys.length < 1 || keys.length > SFC_CANDIDATE_KEYS.length || keys.some((key) => !isSfcCandidateKey(key))) return false

  return keys.every((rawKey) => {
    const key = rawKey as SfcCandidateKey
    const candidates = axes[key]
    if (!Array.isArray(candidates) || candidates.length === 0) return false
    return candidates.every((item) => typeof item === 'string' || (typeof item === 'number' && Number.isFinite(item)))
  })
}

/** The pinned form always submits all eight dimensions, unlike the general wire contract. */
export function isValidPinnedSfcCandidateAxes(value: unknown): boolean {
  return isValidSfcCandidateAxes(value) && SFC_CANDIDATE_KEYS.every((key) => Object.prototype.hasOwnProperty.call(value, key))
}

export interface SfcSinglePredictionFields {
  scope: 'all_column' | 'single_column'
  threshold: string
  model_id: string
  single_column_group?: string
  smiles_1: string
  smiles_2: string
  modifier_total_ratio: number
  modifiers: SfcModifier[]
  auto_normalize: boolean
  candidates: SfcCandidateSpace
  top_k: number
  persist: true
}

export interface SfcBatchRow {
  smiles_1: string
  smiles_2: string
  modifier_total_ratio: number
  modifiers: SfcModifier[]
  candidates: SfcCandidateSpace
}

export interface SfcBatchPredictionFields {
  scope: 'batch'
  threshold: string
  model_id: string
  rows: SfcBatchRow[]
  auto_normalize: boolean
  top_k: number
  persist: true
}

export interface SfcFormSubmit { top_k: number }

