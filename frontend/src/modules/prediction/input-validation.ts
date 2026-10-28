export function areDistinctStructureRefs(refs: readonly (string | null | undefined)[], required = 2): boolean {
  if (refs.length !== required) return false
  const normalized = refs.map((ref) => ref?.trim())
  return normalized.every((ref): ref is string => Boolean(ref)) && new Set(normalized).size === required
}
