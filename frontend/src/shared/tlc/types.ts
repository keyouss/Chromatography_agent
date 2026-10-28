import type { ContractTlcInput, ContractTlcSolventComponent } from '@/core/contract-types'

export type TlcInput = ContractTlcInput
export type TlcSolventComponent = ContractTlcSolventComponent
export type TlcSolventId = TlcSolventComponent['solvent_id']
export const TLC_SOLVENT_IDS = ['hexane', 'ethyl_acetate', 'dichloromethane', 'methanol', 'diethyl_ether'] as const
export function isTlcSolventId(value: string): value is TlcSolventId {
  return (TLC_SOLVENT_IDS as readonly string[]).includes(value)
}
export function parseTlcFraction(value: string | number): number {
  if (typeof value === 'number') return value
  const text = value.trim()
  if (text.includes(':')) {
    const [left, right] = text.split(':').map(Number)
    return Number.isFinite(left) && Number.isFinite(right) && left >= 0 && right >= 0 && left + right > 0 ? left / (left + right) : Number.NaN
  }
  return Number(text)
}

export type TlcTool = 'single' | 'batch' | 'condition_compare' | 'reverse_solvent'

export interface TlcSolventOption {
  id: string
  label?: string
}

export interface TlcCandidateSystem {
  id: string
  solvent_ids: readonly string[]
  label?: string
}

/** Capability values are supplied by the server; the panel does not invent a scan range. */
export interface TlcReverseRange {
  target_rf: { min: number; max: number }
  ratio_step: { min: number; max: number; min_inclusive?: boolean; max_inclusive?: boolean }
  max_candidates: number
  available?: boolean
  unavailable_reason?: string | null
}

function isTlcReverseRatioRangeValid(range: TlcReverseRange['ratio_step']): boolean {
  return Number.isFinite(range.min) && Number.isFinite(range.max) &&
    range.min >= 0 && range.max <= 1 && range.min <= range.max &&
    (range.min_inclusive === undefined || typeof range.min_inclusive === 'boolean') &&
    (range.max_inclusive === undefined || typeof range.max_inclusive === 'boolean')
}

export function firstTlcReverseRatioStep(range: TlcReverseRange['ratio_step']): number | null {
  if (!isTlcReverseRatioRangeValid(range)) return null
  const lowerInclusive = range.min_inclusive !== false
  const upperInclusive = range.max_inclusive !== false
  if (lowerInclusive && range.min > 0 && (upperInclusive || range.min < range.max)) {
    return isTlcReverseRatioStepAllowed(range.min, range) ? range.min : null
  }
  const candidate = range.min > 0 ? (range.min + range.max) / 2 : range.max / 2
  return isTlcReverseRatioStepAllowed(candidate, range) ? candidate : null
}

export function isTlcReverseRangeValid(range: TlcReverseRange): boolean {
  return Number.isFinite(range.target_rf.min) && Number.isFinite(range.target_rf.max) &&
    range.target_rf.min >= 0 && range.target_rf.max <= 1 && range.target_rf.min <= range.target_rf.max &&
    isTlcReverseRatioRangeValid(range.ratio_step) && firstTlcReverseRatioStep(range.ratio_step) !== null &&
    Number.isInteger(range.max_candidates) && range.max_candidates >= 1 && range.max_candidates <= 200
}

export function mapTlcReverseRange(range: TlcReverseRange | null | undefined): TlcReverseRange {
  if (!range) return {
    target_rf: { min: 0, max: 0 }, ratio_step: { min: 0, max: 0 }, max_candidates: 0,
    available: false, unavailable_reason: '服务端未返回 TLC 反推能力。',
  }
  if (range.available === false) return { ...range, available: false }
  const available = isTlcReverseRangeValid(range)
  return {
    ...range,
    available,
    unavailable_reason: available ? range.unavailable_reason : '服务端返回的 TLC 反推能力范围无效。',
  }
}

export function isTlcReverseRatioStepAllowed(value: number | null, range: TlcReverseRange['ratio_step']): boolean {
  return value !== null && Number.isFinite(value) && value > 0 && value <= 1 && isTlcReverseRatioRangeValid(range) &&
    (range.min_inclusive === false ? value > range.min : value >= range.min) &&
    (range.max_inclusive === false ? value < range.max : value <= range.max)
}

export interface TlcReverseSubmit {
  target_rf: { min: number; max: number }
  candidate_system_ids: readonly string[]
  ratio_step: number
  max_candidates: number
}
export type TlcComparisonSubmit = readonly TlcInput[]

export interface TlcBatchSubmit {
  input_artifact_id: string
  common_input: TlcInput
}

export type TlcResultState = 'empty' | 'loading' | 'success' | 'unreachable' | 'partial_failure' | 'failed'

export interface TlcBatchSummary {
  total: number
  valid: number
  invalid: number
  failed?: number
}
