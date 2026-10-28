const GC_QUANTILES = [
  ['q10', 'Q10'],
  ['q50', 'Q50'],
  ['q90', 'Q90'],
] as const

export type GcQuantileDisplayRow = { metric: string; value: number; unit: 'min' }

export function gcQuantileDisplayRows(value: unknown): GcQuantileDisplayRow[] | null {
  if (value === undefined) return []
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const quantiles = value as Record<string, unknown>
  if (Object.values(quantiles).some((result) => typeof result !== 'number' || !Number.isFinite(result))) return null
  const rows: GcQuantileDisplayRow[] = []
  for (const [field, label] of GC_QUANTILES) {
    const result = quantiles[field]
    if (result === undefined) continue
    if (typeof result !== 'number') return null
    rows.push({ metric: label, value: result, unit: 'min' })
  }
  return rows
}
