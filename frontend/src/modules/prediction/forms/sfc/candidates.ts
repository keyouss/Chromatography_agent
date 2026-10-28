import { SFC_CANDIDATE_KEYS, type SfcCandidateKey, type SfcCandidateSpace, type SfcModifier } from './types'

export const DEFAULT_CANDIDATE_LIMIT = undefined

export function emptyCandidateSpace(): SfcCandidateSpace {
  return Object.fromEntries(SFC_CANDIDATE_KEYS.map((key) => [key, []])) as unknown as SfcCandidateSpace
}

export function candidateCount(candidates: Partial<Record<SfcCandidateKey, readonly string[]>>): number {
  return SFC_CANDIDATE_KEYS.reduce((count, key) => count * (candidates[key]?.length || 0), 1)
}

export function cloneCandidateSpace(source: Partial<Record<SfcCandidateKey, readonly string[]>>): SfcCandidateSpace {
  return Object.fromEntries(SFC_CANDIDATE_KEYS.map((key) => [key, [...(source[key] ?? [])]])) as unknown as SfcCandidateSpace
}

export function normalizedModifiers(modifiers: readonly SfcModifier[], autoNormalize: boolean): SfcModifier[] {
  const total = modifiers.reduce((sum, item) => sum + item.ratio, 0)
  if (!autoNormalize || total <= 0) return modifiers.map((item) => ({ ...item }))
  return modifiers.map((item) => ({ ...item, ratio: item.ratio / total }))
}

export function modifierRatioTotal(modifiers: readonly SfcModifier[]): number {
  return modifiers.reduce((sum, item) => sum + item.ratio, 0)
}
